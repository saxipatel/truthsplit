"""Metric DNA: a side-by-side description of two metric definitions and of the sources they ran on.

Definition fields come from the metric definitions. Source facts (query joins, grain, window) come from the
dataset's provenance; for files there are none to report. Nothing here is inferred from arbitrary SQL.
"""

from app.comparison.metrics import MetricSpec

# Replay order of definition differences. Each entry: (step id, finding type, fields it switches).
REPLAY_STEPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("date_window", "date_cutoff", ("window_start", "window_end", "window_end_inclusive", "timezone")),
    ("filters", "filter_difference", ("filters",)),
    ("refund_policy", "refund_policy", ("refund_policy",)),
    ("grain", "definition_difference", ("grain",)),
    ("null_policy", "definition_difference", ("null_policy",)),
    ("value_field", "definition_difference", ("value_field",)),
]


def _filter_key(f) -> str:
    value = f.value if not isinstance(f.value, list) else sorted(str(v) for v in f.value)
    return f"{f.field} {f.op} {value}"


def _window_text(spec: MetricSpec) -> str | None:
    if not (spec.window_start or spec.window_end):
        return None
    start, end = spec.window_utc()
    bracket = "]" if spec.window_end_inclusive else ")"
    local = f"[{spec.window_start or '-inf'}, {spec.window_end or '+inf'}{bracket} {spec.timezone}"
    utc = f"[{start.isoformat() if start else '-inf'}, {end.isoformat() if end else '+inf'}{bracket} UTC"
    return f"{local} = {utc}"


def describe(spec: MetricSpec) -> dict:
    return {
        "aggregation": spec.aggregation.value,
        "value_field": spec.value_field,
        "grain": spec.grain.value,
        "window": _window_text(spec),
        "filters": sorted(_filter_key(f) for f in spec.filters),
        "refund_policy": spec.refund_policy.value,
        "null_policy": spec.null_policy.value,
        "currency": spec.currency,
    }


def source_facts(dataset) -> dict:
    meta = dataset.schema_json or {}
    facts = {"source_type": dataset.source_type.value, "label": dataset.label, "rows": dataset.row_count}
    if dataset.source_type.value == "postgres_query":
        facts.update(
            query_key=dataset.query_key,
            query_params=dataset.query_params,
            sql_sha256=meta.get("sql_sha256"),
            tables=meta.get("tables"),
            joins=meta.get("joins"),
            grain=meta.get("grain"),
            measure_table=meta.get("measure_table"),
            query_window=meta.get("window"),
            executed_at=meta.get("executed_at"),
        )
    return facts


def compare(left: MetricSpec, right: MetricSpec, left_dataset=None, right_dataset=None) -> dict:
    """Field-by-field comparison. `differs` marks every field the replay will switch or that blocks comparison."""
    a, b = describe(left), describe(right)
    fields = [{"field": k, "left": a[k], "right": b[k], "differs": a[k] != b[k]} for k in a]
    left_only = sorted(set(a["filters"]) - set(b["filters"]))
    right_only = sorted(set(b["filters"]) - set(a["filters"]))
    out = {"fields": fields, "filters_left_only": left_only, "filters_right_only": right_only}
    if left_dataset is not None and right_dataset is not None:
        out["sources"] = {"left": source_facts(left_dataset), "right": source_facts(right_dataset)}
    return out
