#!/usr/bin/env python3
"""Reproducible end-to-end benchmark against a running TruthSplit stack.

For each size N it generates two CSV files from a fixed seed (left: N orders; right: about 1% of keys
removed, about 1% changed by +1.00, about 0.5% duplicated exactly, plus 500 extra keys), then times, through
the HTTP API: upload, import, investigation (3 runs), what-if simulation (3 runs), dataset profile, and
HTML report export. Every investigation's findings are checked against the planted differences, so a run that
is fast but wrong fails.

    python3 scripts/benchmark.py --sizes 10000,100000,250000 --out docs/benchmarks/results.json

Standard library only. Each run gets a fresh batch tag so imports are measured on new data rather than
returning an already-imported dataset; the generated values are otherwise identical for a given size.
"""

import argparse
import io
import json
import platform
import random
import statistics
import subprocess
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from decimal import Decimal

SEED = 20261022
HEADER = "id,order,amount,refund,ccy,at,status,region,batch\n"
MAPPING = {
    "source_record_id": "id",
    "business_key": ["order"],
    "amount": "amount",
    "refund_amount": "refund",
    "currency": "ccy",
    "occurred_at": "at",
    "status": "status",
    "attributes": ["region"],
}


def generate(n: int, batch: str) -> tuple[bytes, bytes, dict]:
    rnd = random.Random(SEED + n)
    left, right = [HEADER], [HEADER]
    expected = {"missing_records": Decimal(0), "extra_records": Decimal(0), "duplicate_records": Decimal(0), "amount_mismatch": Decimal(0)}
    for i in range(n):
        amount = Decimal(rnd.randint(100, 99999)) / 100
        row = "r{i},O{i:07d},{amt},,USD,2026-03-{d:02d}T{h:02d}:15:00Z,paid,{region},{batch}\n"
        fields = {"i": i, "d": i % 28 + 1, "h": i % 24, "region": ("EU", "US", "APAC")[i % 3], "batch": batch}
        left.append(row.format(amt=amount, **fields))
        u = rnd.random()
        if u < 0.01:
            expected["missing_records"] -= amount
            continue
        if u < 0.02:
            right.append(row.format(amt=amount + 1, **fields))
            expected["amount_mismatch"] += 1
            continue
        right.append(row.format(amt=amount, **fields))
        if u < 0.025:
            right.append(row.format(amt=amount, **fields))
            expected["duplicate_records"] += amount
    for j in range(500):
        right.append(f"x{j},X{j:07d},10.00,,USD,2026-03-05T10:00:00Z,paid,EU,{batch}\n")
        expected["extra_records"] += Decimal(10)
    return "".join(left).encode(), "".join(right).encode(), expected


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None):
        req = urllib.request.Request(self.base + path, data=body, method=method)
        if ctype:
            req.add_header("Content-Type", ctype)
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = resp.read()
        elapsed = time.perf_counter() - t0
        return (json.loads(data) if resp.headers.get_content_type() == "application/json" else data), elapsed

    def json(self, method: str, path: str, payload: dict):
        return self.call(method, path, json.dumps(payload).encode(), "application/json")

    def upload(self, name: str, content: bytes):
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: text/csv\r\n\r\n'.encode())
        body.write(content)
        body.write(f"\r\n--{boundary}--\r\n".encode())
        return self.call("POST", "/api/imports/file", body.getvalue(), f"multipart/form-data; boundary={boundary}")


def peak_rss_mib() -> float | None:
    """Peak resident memory of the backend's uvicorn process (VmHWM), if Docker Compose is available."""
    script = 'for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in *bin/uvicorn*) grep VmHWM $p/status;; esac; done'
    try:
        out = subprocess.run(["docker", "compose", "exec", "-T", "backend", "sh", "-c", script], capture_output=True, text=True, timeout=30).stdout
        return round(int(out.split()[1]) / 1024, 1)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None


def environment() -> dict:
    env = {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine()}
    for key, cmd in {
        "cpu": ["sysctl", "-n", "machdep.cpu.brand_string"],
        "docker": ["docker", "info", "--format", "{{.ServerVersion}} · VM {{.NCPU}} CPUs · {{.MemTotal}} bytes"],
    }.items():
        try:
            env[key] = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip() or None
        except OSError:
            env[key] = None
    return env


def run_size(api: Api, n: int, repeats: int) -> dict:
    batch = uuid.uuid4().hex[:8]
    left_csv, right_csv, expected = generate(n, batch)
    result: dict = {"rows_left": n, "rows_right": right_csv.count(b"\n") - 1, "bytes_left": len(left_csv), "bytes_right": len(right_csv)}

    datasets = []
    for side, content in (("left", left_csv), ("right", right_csv)):
        up, t_up = api.upload(f"bench-{n}-{side}-{batch}.csv", content)
        ds, t_imp = api.json("POST", "/api/datasets", {"upload_id": up["upload"]["id"], "mapping": MAPPING})
        result[f"upload_{side}_s"] = round(t_up, 3)
        result[f"import_{side}_s"] = round(t_imp, 3)
        datasets.append(ds["dataset"]["id"])

    metric, _ = api.json("POST", "/api/metric-definitions", {"name": f"benchmark {n} {batch}", "aggregation": "sum"})
    inv_times, inv = [], None
    for k in range(repeats):
        inv, t = api.json("POST", "/api/investigations", {
            "title": f"Benchmark {n} rows ({batch}) run {k + 1}",
            "left": {"dataset_id": datasets[0], "metric_definition_id": metric["id"]},
            "right": {"dataset_id": datasets[1], "metric_definition_id": metric["id"]},
        })
        inv_times.append(t)
        got = {f["type"]: Decimal(f["amount_impact"]) for f in inv["findings"] if f["amount_impact"] is not None}
        wrong = {k2: (str(got.get(k2)), str(v)) for k2, v in expected.items() if got.get(k2, Decimal(0)) != v}
        if wrong or Decimal(inv["unexplained_delta"]) != 0:
            raise SystemExit(f"Investigation results differ from the planted differences at N={n}: {wrong}")
    result["investigation_s"] = [round(t, 3) for t in inv_times]
    result["investigation_median_s"] = round(statistics.median(inv_times), 3)
    result["delta"] = inv["baseline_delta"]
    result["findings_checked"] = {k: str(v) for k, v in expected.items()}

    sim_times = []
    for _ in range(repeats):
        sim, t = api.json("POST", f"/api/investigations/{inv['id']}/simulations", {"rule_type": "remove_duplicates", "side": "right", "kind": "exact"})
        sim_times.append(t)
        if Decimal(sim["after_delta"]) != Decimal(inv["baseline_delta"]) - expected["duplicate_records"]:
            raise SystemExit(f"Simulation result is wrong at N={n}")
    result["simulation_s"] = [round(t, 3) for t in sim_times]
    result["simulation_median_s"] = round(statistics.median(sim_times), 3)

    _, t = api.call("GET", f"/api/datasets/{datasets[1]}/profile")
    result["profile_right_s"] = round(t, 3)
    report, t = api.call("GET", f"/api/investigations/{inv['id']}/export?format=html")
    result["report_html_s"] = round(t, 3)
    result["report_html_bytes"] = len(report)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--sizes", default="10000,100000,250000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    api = Api(args.base_url)
    runs = []
    for n in (int(x) for x in args.sizes.split(",")):
        print(f"N={n:,} ...", flush=True)
        r = run_size(api, n, args.repeats)
        print(
            f"  import L/R {r['import_left_s']}s / {r['import_right_s']}s · investigation median {r['investigation_median_s']}s "
            f"· simulation median {r['simulation_median_s']}s · profile {r['profile_right_s']}s · report {r['report_html_s']}s",
            flush=True,
        )
        runs.append(r)
    output = {
        "measured_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "repeats": args.repeats,
        "base_url": args.base_url,
        "environment": environment(),
        "backend_peak_rss_mib": peak_rss_mib(),
        "runs": runs,
    }
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(output, fh, indent=2)
            fh.write("\n")
        print(f"wrote {args.out}")
    print(f"backend peak RSS: {output['backend_peak_rss_mib']} MiB")


if __name__ == "__main__":
    main()
