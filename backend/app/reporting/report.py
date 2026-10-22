"""Investigation reports: one JSON document with everything stored about an investigation, and a readable,
self-contained HTML rendering of it. All text in the HTML is escaped; the page has no scripts."""

from datetime import UTC, datetime
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.memory.service import history
from app.models import ComparisonResult, EvidenceRecord, Investigation, ResolutionNote, Simulation

EVIDENCE_SAMPLE = 20

NEXT_STEPS = {
    "data.missing_keys": "Check whether the listed keys failed to load into the right source, or were excluded on purpose.",
    "data.extra_keys": "Check why the listed keys exist only on the right: late loads, test data or a broader scope.",
    "data.exact_duplicates": "Find the step that emits identical rows (double export, re-run load) and de-duplicate at source.",
    "data.value_mismatch": "Review the listed keys' source rows; differences that are not refunds or join copies are usually keying or rounding errors.",
    "data.join_amplification": "Report the measure at one row per business key (aggregate the child table before joining, or use DISTINCT ON).",
    "data.query_window": "Agree one reporting window and time zone, or label each report with its window.",
    "data.near_cutoff": "Confirm each source's reporting window; the timing pattern suggests a cutoff or time-zone difference.",
    "data.refund_identity": "Agree whether the metric is gross or net of refunds, and make the source amounts consistent with that.",
    "data.non_unique_keys": "Confirm whether repeated keys are legitimate child rows or duplication.",
    "data.unmatchable_rows": "Rows without a business key cannot be reconciled; add a key at source or investigate them manually.",
    "definition.date_window": "The definitions use different windows; agree which one the business owner approves.",
    "definition.filters": "The definitions filter records differently; agree the canonical filter.",
    "definition.refund_policy": "The definitions treat refunds differently; agree the canonical refund policy.",
    "definition.grain": "The definitions aggregate at different grains; agree the canonical grain.",
    "definition.null_policy": "The definitions handle missing values differently; agree the canonical policy.",
    "definition.value_field": "The definitions measure different fields; confirm which field the metric should use.",
}


def _money(value) -> str | None:
    return None if value is None else str(value)


def build_report(session: Session, inv: Investigation) -> dict:
    comparison = session.get(ComparisonResult, inv.id)
    findings = []
    for f in inv.findings:
        count = session.scalar(select(func.count()).where(EvidenceRecord.finding_id == f.id))
        sample = session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.finding_id == f.id)
            .order_by(EvidenceRecord.id)
            .limit(EVIDENCE_SAMPLE)
        )
        findings.append(
            {
                "id": str(f.id),
                "step_order": f.step_order,
                "type": f.type.value,
                "evidence_status": f.evidence_status.value,
                "rule_id": f.rule_id,
                "title": f.title,
                "description": f.description,
                "amount_impact": _money(f.amount_impact),
                "classification_reason": f.classification_reason,
                "assumptions": f.assumptions_json,
                "calculation": f.calculation_json,
                "overlap_note": f.overlap_note,
                "proof_hash": f.proof_hash,
                "evidence_rows": count,
                "evidence_sample": [
                    {
                        "side": e.side.value,
                        "business_key": e.business_key,
                        "source_record_id": e.source_record_id,
                        "left_amount": _money(e.left_amount),
                        "right_amount": _money(e.right_amount),
                        "contribution": _money(e.contribution),
                    }
                    for e in sample
                ],
            }
        )
    simulations = session.scalars(
        select(Simulation).where(Simulation.investigation_id == inv.id).order_by(Simulation.created_at)
    )
    notes = session.scalars(
        select(ResolutionNote)
        .where(ResolutionNote.investigation_id == inv.id)
        .order_by(ResolutionNote.reviewed_at)
    )
    rules = {f["rule_id"] for f in findings}
    config = inv.config_json or {}
    return {
        "report_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "synthetic_demo_data": inv.is_demo,
        "investigation": {
            "id": str(inv.id),
            "title": inv.title,
            "status": inv.status.value,
            "created_at": inv.created_at.isoformat(),
            "engine_version": inv.engine_version,
            "delta_convention": "delta = right - left",
        },
        "totals": {
            "left": _money(inv.baseline_left),
            "right": _money(inv.baseline_right),
            "delta": _money(inv.baseline_delta),
            "explained": _money(inv.explained_delta),
            "unexplained": _money(inv.unexplained_delta),
            "currency": config.get("currency"),
        },
        "inputs": [
            {
                "side": i.side.value,
                "dataset_id": str(i.dataset_id),
                "label": i.dataset.label,
                "source_type": i.dataset.source_type.value,
                "fingerprint": i.dataset_fingerprint,
                "rows": i.row_count,
            }
            for i in sorted(inv.inputs, key=lambda i: i.side.value)
        ],
        "metric_definitions": {
            side: {"id": str(m.id), "name": m.name, "version": m.version}
            for side, m in (("left", inv.left_metric), ("right", inv.right_metric))
        },
        "metric_dna": config.get("metric_dna"),
        "proof_queries": {
            side: {"sql": q["sql"], "query_hash": q["query_hash"]}
            for side, q in (config.get("proof_queries") or {}).items()
        },
        "attribution_order": config.get("attribution_order"),
        "replay": config.get("replay"),
        "comparison": None
        if comparison is None
        else {
            k: getattr(comparison, k)
            for k in (
                "matched_keys",
                "left_only_keys",
                "right_only_keys",
                "left_duplicate_keys",
                "right_duplicate_keys",
                "left_null_keys",
                "right_null_keys",
                "mismatched_keys",
            )
        },
        "findings": findings,
        "simulations": [
            {
                "id": str(s.id),
                "rule_type": s.rule_type.value,
                "parameters": s.parameters_json,
                "before_delta": _money(s.before_delta),
                "after_delta": _money(s.after_delta),
                "resolved_amount": _money(s.resolved_amount),
                "affected_rows": s.affected_rows,
                "created_at": s.created_at.isoformat(),
            }
            for s in simulations
        ],
        "notes": [
            {
                "decision": n.decision.value,
                "note": n.note,
                "finding_id": str(n.finding_id) if n.finding_id else None,
                "reviewer": n.reviewer,
                "reviewed_at": n.reviewed_at.isoformat(),
            }
            for n in notes
        ],
        "history": [
            {"action": e.action, "actor": e.actor, "occurred_at": e.occurred_at.isoformat()}
            for e in history(session, inv)
        ],
        "recommended_next_steps": [
            {"rule_id": r, "step": NEXT_STEPS[r]} for r in sorted(rules) if r in NEXT_STEPS
        ],
        "limitations": [
            "Per-cause amounts depend on the documented attribution order; their sum does not.",
            "Confirmed means reproduced from listed records under stated assumptions, not that a root cause is proven beyond them.",
            "Suspected findings carry no attributed amount.",
        ],
    }


def _row(cells: list, header: bool = False) -> str:
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{escape('' if c is None else str(c))}</{tag}>" for c in cells) + "</tr>"


def render_html(report: dict) -> str:
    inv, totals = report["investigation"], report["totals"]
    e = escape
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(inv['title'])} — TruthSplit report</title>",
        "<style>body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0b0b0b;background:#fcfcfb;margin:0;padding:24px;max-width:1100px}"
        "h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #e1e0d9;padding-bottom:4px}"
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}th,td{border:1px solid #e1e0d9;padding:4px 6px;text-align:left;vertical-align:top}"
        "td.n{text-align:right;font-variant-numeric:tabular-nums}.muted{color:#52514e}.banner{background:#fff6dd;border:1px solid #eda100;padding:8px;margin:12px 0}"
        "pre{white-space:pre-wrap;font-size:12px;background:#f3f2ee;padding:8px}</style></head><body>",
        f"<h1>{e(inv['title'])}</h1>",
        f"<p class='muted'>Investigation {e(inv['id'])} · status {e(inv['status'])} · created {e(inv['created_at'])} · report generated {e(report['generated_at'])} · engine {e(str(inv['engine_version']))}</p>",
    ]
    if report["synthetic_demo_data"]:
        parts.append("<div class='banner'>This investigation uses synthetic demo data.</div>")
    parts += [
        "<h2>Totals</h2><table>",
        _row(["Left", "Right", "Delta (right − left)", "Explained", "Unexplained", "Currency"], header=True),
        _row(
            [
                totals["left"],
                totals["right"],
                totals["delta"],
                totals["explained"],
                totals["unexplained"],
                totals["currency"],
            ]
        ),
        "</table><h2>Inputs</h2><table>",
        _row(["Side", "Dataset", "Source type", "Rows", "Fingerprint"], header=True),
        *[
            _row([i["side"], i["label"], i["source_type"], i["rows"], i["fingerprint"]])
            for i in report["inputs"]
        ],
        "</table>",
    ]
    dna = report.get("metric_dna") or {}
    if dna.get("fields"):
        md = report["metric_definitions"]
        parts += [
            "<h2>Metric definitions</h2><table>",
            _row(
                [
                    "Field",
                    f"Left: {md['left']['name']} v{md['left']['version']}",
                    f"Right: {md['right']['name']} v{md['right']['version']}",
                    "Differs",
                ],
                header=True,
            ),
            *[
                _row([f["field"], f["left"], f["right"], "yes" if f["differs"] else ""])
                for f in dna["fields"]
            ],
            "</table>",
        ]
    parts += [
        "<h2>Discrepancy breakdown</h2><table>",
        _row(["#", "Finding", "Status", "Amount", "Rule", "Evidence rows"], header=True),
    ]
    for f in report["findings"]:
        parts.append(
            _row(
                [
                    f["step_order"],
                    f["title"],
                    f["evidence_status"],
                    f["amount_impact"],
                    f["rule_id"],
                    f["evidence_rows"],
                ]
            )
        )
    parts.append("</table>")
    for status_label, status in (
        ("Confirmed findings", "confirmed"),
        ("Suspected findings", "suspected"),
        ("Unexplained", "unexplained"),
    ):
        selected = [f for f in report["findings"] if f["evidence_status"] == status]
        if not selected:
            continue
        parts.append(f"<h2>{status_label}</h2>")
        for f in selected:
            parts += [
                f"<h3>{e(f['title'])}</h3><p>{e(f['description'])}</p>",
                f"<p class='muted'>Why {e(status)}: {e(f['classification_reason'])}</p>",
            ]
            if f["assumptions"]:
                parts.append("<p class='muted'>Assumptions: " + e("; ".join(f["assumptions"])) + "</p>")
            if f["overlap_note"]:
                parts.append(f"<p class='muted'>Overlap: {e(f['overlap_note'])}</p>")
            if f["evidence_sample"]:
                parts += [
                    f"<table>{_row(['Side', 'Business key', 'Left', 'Right', 'Contribution'], header=True)}",
                    *[
                        _row(
                            [
                                s["side"],
                                s["business_key"],
                                s["left_amount"],
                                s["right_amount"],
                                s["contribution"],
                            ]
                        )
                        for s in f["evidence_sample"]
                    ],
                    "</table>",
                    f"<p class='muted'>Showing {len(f['evidence_sample'])} of {f['evidence_rows']} evidence rows. Proof hash {e(str(f['proof_hash']))}.</p>",
                ]
    if report["simulations"]:
        parts += [
            "<h2>Simulations</h2><table>",
            _row(
                ["Correction", "Parameters", "Delta before", "Delta after", "Resolved", "Affected rows"],
                header=True,
            ),
        ]
        parts += [
            _row(
                [
                    s["rule_type"],
                    s["parameters"],
                    s["before_delta"],
                    s["after_delta"],
                    s["resolved_amount"],
                    s["affected_rows"],
                ]
            )
            for s in report["simulations"]
        ]
        parts.append("</table>")
    if report["notes"]:
        parts += ["<h2>Review notes</h2><table>", _row(["When", "Reviewer", "Decision", "Note"], header=True)]
        parts += [_row([n["reviewed_at"], n["reviewer"], n["decision"], n["note"]]) for n in report["notes"]]
        parts.append("</table>")
    if report["recommended_next_steps"]:
        parts.append("<h2>Recommended next steps</h2><ul>")
        parts += [
            f"<li>{e(s['step'])} <span class='muted'>({e(s['rule_id'])})</span></li>"
            for s in report["recommended_next_steps"]
        ]
        parts.append("</ul>")
    if report["proof_queries"]:
        parts.append("<h2>Proof queries</h2>")
        for side, q in report["proof_queries"].items():
            parts.append(
                f"<p class='muted'>{e(side)} · hash {e(q['query_hash'])}</p><pre>{e(q['sql'])}</pre>"
            )
    parts.append(
        "<h2>Limitations</h2><ul>"
        + "".join(f"<li>{e(x)}</li>" for x in report["limitations"])
        + "</ul></body></html>"
    )
    return "".join(parts)
