#!/usr/bin/env python3
"""Check that a running TruthSplit stack reproduces the demo manifest.

Loads the demo investigations through the API (idempotent) and compares their totals and attributed findings
with sample_data/manifest.json, whose values come from an independent model of the source data.
Standard library only:  python3 scripts/verify_demo.py [base_url]
"""

import json
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "sample_data" / "manifest.json").read_text())


def call(path: str, method: str = "GET"):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def same(got, want) -> bool:
    """Amounts compare as decimals (the API returns scale 4, the manifest scale 2); anything else exactly."""
    if want is None or got is None:
        return got == want
    try:
        return Decimal(str(got)) == Decimal(str(want))
    except ArithmeticError:
        return got == want


def main() -> int:
    loaded = call("/api/demo", "POST")
    failures = 0
    for sc in MANIFEST["scenarios"]:
        inv = call(f"/api/investigations/{loaded[sc['id']]['investigation_id']}")
        exp = sc["expected"]
        checks = [
            ("left total", inv["baseline_left"], exp["left_total"]),
            ("right total", inv["baseline_right"], exp["right_total"]),
            ("difference", inv["baseline_delta"], exp["delta"]),
            ("unexplained", inv["unexplained_delta"], exp["unexplained"]),
        ]
        found = {(f["type"], f["rule_id"]): f for f in inv["findings"]}
        for issue in sc["issues"]:
            f = found.get((issue["category"], issue["rule_id"]))
            if issue["expected_contribution"] is None:
                checks.append((f"{issue['id']} {issue['category']} has no attributed amount", f and f["amount_impact"], None))
            elif sc["id"] == "B" and issue["category"] == "amount_mismatch":
                checks.append((f"{issue['id']} mismatch finding", f and f["amount_impact"], exp["amount_mismatch_finding"]))
            else:
                matches = [x for x in inv["findings"] if (x["type"], x["rule_id"]) == (issue["category"], issue["rule_id"])]
                got = next((x["amount_impact"] for x in matches if x["amount_impact"] is not None and Decimal(x["amount_impact"]) == Decimal(issue["expected_contribution"])), matches[0]["amount_impact"] if matches else None)
                checks.append((f"{issue['id']} {issue['category']}", got, issue["expected_contribution"]))
            if f:
                checks.append((f"{issue['id']} status", f["evidence_status"], issue["expected_status"]))
        print(f"Scenario {sc['id']}: {sc['title']}")
        for name, got, want in checks:
            ok = same(got, want)
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got} (expected {want})")
    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) failed.'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
