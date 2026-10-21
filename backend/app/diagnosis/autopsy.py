"""Difference Autopsy: attribute delta = right - left to causes, in a fixed and documented order.

1. Definition replay. The left definition is switched to the right one a rule at a time, in REPLAY_STEPS
   order (window/timezone, filters, refund policy, grain, null policy, value field), and re-evaluated on the
   left data. Step i contributes metric_after_i - metric_before_i. Each step's standalone effect (that rule
   alone, from the original left definition) is also computed; when it differs, the causes overlap and
   the finding says so. Ordered contributions are not independent causal effects.

2. Join amplification. With both sides now under the right definition, keys whose rows repeat one
   identical amount across otherwise different rows are a fan-out signature. If the side's query
   metadata declares a 1:N join starting at the measure's table, the extra copies are confirmed and removed.
   Otherwise the pattern is reported as suspected, without an attributed amount.

3. Record-level reconciliation (app/comparison/matching.py) of what remains, refined by two rules:
   - date cutoff: one-sided keys whose timestamps fall outside the other side's declared query window;
   - refund treatment: matched keys whose difference equals, to the cent, the refund recorded for them.

4. What is left (rows without a key) is the unexplained remainder. Σ attributed + unexplained == delta is
   asserted on every run.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.comparison.findings import EvidenceDraft, FindingDraft, build_findings
from app.comparison.matching import KeyDiff, check_comparable, reconcile
from app.comparison.metrics import (
    Contribution,
    Evaluation,
    MetricEvaluationError,
    MetricSpec,
    Record,
    evaluate,
    unmapped_fields,
)
from app.diagnosis.dna import REPLAY_STEPS, _filter_key, compare
from app.models.investigations import EvidenceStatus, FindingType, Side

ZERO = Decimal(0)
CUTOFF_PROXIMITY = timedelta(hours=24)


@dataclass
class SideInput:
    records: list[Record]
    spec: MetricSpec
    mapping: dict
    source_type: str = "csv"
    provenance: dict = field(default_factory=dict)  # datasets.schema_json
    label: str = ""

    @property
    def keys(self) -> set[str]:
        return {r.business_key for r in self.records if r.business_key is not None}


@dataclass
class ReplayStep:
    step: str
    finding_type: str
    status: str  # applied | no_effect | not_replayable | identical
    metric_before: Decimal | None = None
    metric_after: Decimal | None = None
    contribution: Decimal | None = None
    standalone: Decimal | None = None
    detail: str | None = None

    def as_json(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


@dataclass
class AutopsyResult:
    left_value: Decimal
    right_value: Decimal
    findings: list[FindingDraft]
    replay: list[ReplayStep]
    dna: dict
    comparison: object  # Reconciliation of the final data-level stage
    left_final: Evaluation
    right_final: Evaluation
    base_left: Evaluation
    base_right: Evaluation

    @property
    def delta(self) -> Decimal:
        return self.right_value - self.left_value

    @property
    def explained(self) -> Decimal:
        return sum(
            (
                f.amount_impact
                for f in self.findings
                if f.amount_impact is not None and f.evidence_status is EvidenceStatus.CONFIRMED
            ),
            ZERO,
        )


# ---------------------------------------------------------------------------- definition replay


def _same(spec_a: MetricSpec, spec_b: MetricSpec, fields: tuple[str, ...]) -> bool:
    for f in fields:
        a, b = getattr(spec_a, f), getattr(spec_b, f)
        if f == "filters":
            if sorted(map(_filter_key, a)) != sorted(map(_filter_key, b)):
                return False
        elif a != b:
            return False
    return True


def _windows_equivalent(a: MetricSpec, b: MetricSpec) -> bool:
    """Two windows written in different zones can denote the same instants."""
    try:
        return a.window_utc() == b.window_utc() and a.window_end_inclusive == b.window_end_inclusive
    except MetricEvaluationError:
        return False


def _changed_records(before: Evaluation, after: Evaluation) -> list[EvidenceDraft]:
    b = {c.record.id: c for c in before.contributions}
    a = {c.record.id: c for c in after.contributions}
    why_before = {r.id: reason for reason, rows in before.excluded.items() for r in rows}
    why_after = {r.id: reason for reason, rows in after.excluded.items() for r in rows}
    records = {c.record.id: c.record for c in before.contributions + after.contributions}
    out = []
    for rid in sorted(records, key=lambda i: records[i].row_number):
        vb = b[rid].value if rid in b else None
        va = a[rid].value if rid in a else None
        if vb == va:
            continue
        r = records[rid]
        out.append(
            EvidenceDraft(
                side=Side.LEFT,
                business_key=r.business_key,
                left_amount=vb,
                right_amount=va,
                contribution=(va or ZERO) - (vb or ZERO),
                detail={
                    "row_number": r.row_number,
                    "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                    "refund_amount": str(r.refund_amount) if r.refund_amount is not None else None,
                    "status": r.status,
                    "before": str(vb) if vb is not None else None,
                    "after": str(va) if va is not None else None,
                    "excluded_before": why_before.get(rid),
                    "excluded_after": why_after.get(rid),
                },
                dataset_record_id=r.id,
                source_record_id=r.source_record_id,
            )
        )
    return out


_STEP_TEXT = {
    "date_window": (
        "Reporting window differs",
        "Aligning the left date window, time zone and end bound to the right definition",
    ),
    "filters": ("Record filters differ", "Applying the right definition's filters to the left data"),
    "refund_policy": ("Refund treatment differs", "Switching the left refund policy to the right one"),
    "grain": ("Aggregation grain differs", "Switching the left grain to the right one"),
    "null_policy": ("Null handling differs", "Switching the left null policy to the right one"),
    "value_field": (
        "Measured field differs",
        "Measuring the right definition's value field on the left data",
    ),
}


def _replay(
    left: SideInput, right: SideInput, base_left: Evaluation
) -> tuple[Evaluation, MetricSpec, list[ReplayStep], list[FindingDraft]]:
    steps: list[ReplayStep] = []
    findings: list[FindingDraft] = []
    spec, current = left.spec, base_left
    for step_id, ftype, fields in REPLAY_STEPS:
        if _same(spec, right.spec, fields):
            continue
        if step_id == "date_window" and _windows_equivalent(spec, right.spec):
            steps.append(
                ReplayStep(
                    step_id,
                    ftype,
                    "identical",
                    detail="Windows are written differently but cover the same instants.",
                )
            )
            spec = spec.model_copy(update={f: getattr(right.spec, f) for f in fields})
            continue
        candidate = spec.model_copy(update={f: getattr(right.spec, f) for f in fields})
        missing = unmapped_fields(candidate, left.mapping)
        title, verb = _STEP_TEXT[step_id]
        if missing:
            steps.append(
                ReplayStep(
                    step_id, ftype, "not_replayable", detail=f"left data does not map {', '.join(missing)}"
                )
            )
            findings.append(
                FindingDraft(
                    type=FindingType(ftype),
                    evidence_status=EvidenceStatus.SUSPECTED,
                    title=f"{title}; effect not measurable",
                    description=f"{verb} needs {', '.join(missing)}, which the left dataset does not map. "
                    "The difference this rule makes stays inside the record-level findings below.",
                    amount_impact=None,
                    rule_id=f"definition.{step_id}",
                    classification_reason="The definitions differ, but the left data lacks the field needed to replay the rule.",
                    calculation={"fields": list(fields), "missing": missing},
                )
            )
            continue
        try:
            after = evaluate(left.records, candidate)
        except MetricEvaluationError as exc:
            steps.append(ReplayStep(step_id, ftype, "not_replayable", detail=str(exc)))
            continue
        contribution = after.value - current.value
        try:
            alone = (
                evaluate(
                    left.records, left.spec.model_copy(update={f: getattr(right.spec, f) for f in fields})
                ).value
                - base_left.value
            )
        except MetricEvaluationError:
            alone = None
        evidence = _changed_records(current, after)
        steps.append(
            ReplayStep(
                step_id,
                ftype,
                "applied" if evidence else "no_effect",
                current.value,
                after.value,
                contribution,
                alone,
            )
        )
        if evidence:
            overlap = None
            if alone is not None and alone != contribution:
                overlap = (
                    f"Applied alone, this rule changes the left metric by {alone}; in the ordered replay it accounts for "
                    f"{contribution} because earlier steps already changed some of the same records. Attribution "
                    "between overlapping causes depends on the replay order."
                )
            findings.append(
                FindingDraft(
                    type=FindingType(ftype),
                    evidence_status=EvidenceStatus.CONFIRMED,
                    title=title,
                    description=f"{verb} changes the left metric from {current.value} to {after.value} "
                    f"({len(evidence)} records affected), accounting for {contribution} of the difference.",
                    amount_impact=contribution,
                    rule_id=f"definition.{step_id}",
                    classification_reason=(
                        "Both definitions are explicit and the effect is recomputed exactly by re-evaluating the left "
                        "records with this rule switched; every affected record is listed with its value before and after."
                    ),
                    assumptions=[
                        "The right definition is used as the reference only to measure the difference; it is not assumed to be correct."
                    ],
                    calculation={
                        "formula": "left metric after step - left metric before step",
                        "before": str(current.value),
                        "after": str(after.value),
                        "standalone_effect": str(alone) if alone is not None else None,
                        "left_rule": _rule_text(spec, fields),
                        "right_rule": _rule_text(right.spec, fields),
                    },
                    overlap_note=overlap,
                    evidence=evidence,
                )
            )
        spec, current = candidate, after
    return current, spec, steps, findings


def _rule_text(spec: MetricSpec, fields: tuple[str, ...]) -> dict:
    out = {}
    for f in fields:
        v = getattr(spec, f)
        out[f] = (
            sorted(map(_filter_key, v))
            if f == "filters"
            else (v.isoformat() if isinstance(v, datetime) else str(v) if v is not None else None)
        )
    return out


# ---------------------------------------------------------------------------- join amplification


def _fan_out(evaluation: Evaluation) -> list[tuple[str, list[Contribution]]]:
    """Keys with several rows that all carry the same value but are not byte-identical copies."""
    by_key: dict[str, list[Contribution]] = defaultdict(list)
    for c in evaluation.contributions:
        if c.record.business_key is not None:
            by_key[c.record.business_key].append(c)
    out = []
    for key in sorted(by_key):
        rows = sorted(by_key[key], key=lambda c: c.record.row_number)
        if (
            len(rows) > 1
            and len({c.value for c in rows}) == 1
            and len({c.record.raw_payload_hash for c in rows}) == len(rows)
        ):
            out.append((key, rows))
    return out


def _without(evaluation: Evaluation, dropped: set[int]) -> Evaluation:
    kept = [c for c in evaluation.contributions if c.record.id not in dropped]
    return Evaluation(
        evaluation.spec,
        sum((c.value for c in kept), ZERO),
        kept,
        evaluation.excluded,
        evaluation.currency,
        evaluation.window_utc,
    )


def _amplification(
    side_label: str, side: SideInput, evaluation: Evaluation, other: Evaluation
) -> tuple[Evaluation, FindingDraft | None]:
    groups = _fan_out(evaluation)
    if not groups or evaluation.spec.aggregation.value != "sum":
        return evaluation, None
    other_values: dict[str, Decimal] = defaultdict(Decimal)
    for c in other.contributions:
        if c.record.business_key is not None:
            other_values[c.record.business_key] += c.value
    excess = sum((rows[0].value * (len(rows) - 1) for _, rows in groups), ZERO)
    corroborated = sum(1 for key, rows in groups if other_values.get(key) == rows[0].value)
    joins = side.provenance.get("amplifying_joins") or []
    sign = 1 if side_label == "right" else -1
    evidence = [
        EvidenceDraft(
            side=Side(side_label),
            business_key=key,
            left_amount=other_values.get(key)
            if side_label == "right"
            else sum((c.value for c in rows), ZERO),
            right_amount=sum((c.value for c in rows), ZERO)
            if side_label == "right"
            else other_values.get(key),
            contribution=sign * rows[0].value * (len(rows) - 1),
            detail={
                "rows": [
                    {"row_number": c.record.row_number, "record_id": c.record.id, "value": str(c.value)}
                    for c in rows
                ],
                "copies": len(rows),
                "other_side_value": str(other_values[key]) if key in other_values else None,
            },
        )
        for key, rows in groups
    ]
    calc = {
        "formula": "sum over fanned-out keys of value x (rows - 1)",
        "keys": len(groups),
        "excess": str(excess),
        "keys_where_other_side_equals_one_copy": corroborated,
        "declared_joins": joins,
        "grain": side.provenance.get("grain"),
    }
    if joins:
        dropped = {c.record.id for _, rows in groups for c in rows[1:]}
        finding = FindingDraft(
            type=FindingType.JOIN_AMPLIFICATION,
            evidence_status=EvidenceStatus.CONFIRMED,
            title=f"Join amplification on the {side_label} ({len(groups)} keys)",
            description=(
                f"The {side_label} query joins {', '.join(j['left'] + ' -> ' + j['right'] for j in joins)} (1:N), so each "
                f"{side.provenance.get('measure_table')} amount repeats once per child row. Recomputing at one row per key "
                f"removes {excess} from the {side_label} metric."
            ),
            amount_impact=sign * excess,
            rule_id="data.join_amplification",
            classification_reason=(
                "Query metadata declares a one-to-many join from the table the amount comes from, and every listed key "
                "has several rows carrying the identical amount. Totals are recomputed at the declared business-key grain."
            ),
            assumptions=["The first row of each key carries the true amount; the others are join copies."],
            calculation=calc,
            evidence=evidence,
        )
        return _without(evaluation, dropped), finding
    for e in evidence:  # nothing is attributed, so the per-key figure is indicative only
        e.detail["indicative_excess"] = str(e.contribution)
        e.contribution = None
    finding = FindingDraft(
        type=FindingType.JOIN_AMPLIFICATION,
        evidence_status=EvidenceStatus.SUSPECTED,
        title=f"Possible join amplification on the {side_label} ({len(groups)} keys)",
        description=(
            f"{len(groups)} keys on the {side_label} have several rows with the same amount but different other columns, "
            f"which is what a one-to-many join produces. If they are join copies they add {excess} to the {side_label} "
            "metric. This is not attributed: the source has no join metadata, and repeated child rows can be legitimate."
        ),
        amount_impact=None,
        rule_id="data.join_amplification",
        classification_reason="Pattern match without source metadata to confirm it.",
        calculation=calc,
        evidence=evidence,
    )
    return evaluation, finding


# ---------------------------------------------------------------------------- data-level refinements


def _window(provenance: dict) -> tuple[datetime, datetime] | None:
    w = provenance.get("window")
    if not w:
        return None
    return datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"])


def _outside(rows: list[Contribution], window: tuple[datetime, datetime]) -> bool:
    start, end = window
    return all(c.record.occurred_at is not None and not (start <= c.record.occurred_at < end) for c in rows)


def _cutoff_finding(
    diffs: list[KeyDiff], side_label: str, other_label: str, window, provenance: dict
) -> FindingDraft:
    sign = 1 if side_label == "right" else -1
    amount = sum((sign * (d.right_value if side_label == "right" else d.left_value) for d in diffs), ZERO)
    w = provenance["window"]
    return FindingDraft(
        type=FindingType.DATE_CUTOFF,
        evidence_status=EvidenceStatus.CONFIRMED,
        title=f"{len(diffs)} {side_label}-only keys fall outside the {other_label} query window",
        description=(
            f"The {other_label} query only returns records in [{w['start']}, {w['end']}) "
            f"(local dates in {w['timezone']}). These {side_label} records are outside it, so they cannot appear on the "
            f"{other_label}. Effect on the difference: {amount}."
        ),
        amount_impact=amount,
        rule_id="data.query_window",
        classification_reason="Every listed record's timestamp lies outside the window recorded in the other snapshot's query provenance.",
        calculation={"other_window": w, "keys": len(diffs)},
        evidence=[
            EvidenceDraft(
                side=Side(side_label),
                business_key=d.key,
                left_amount=d.left_value if d.left else None,
                right_amount=d.right_value if d.right else None,
                contribution=sign * (d.right_value if side_label == "right" else d.left_value),
                detail={
                    "occurred_at": [
                        c.record.occurred_at.isoformat()
                        for c in (d.right if side_label == "right" else d.left)
                    ],
                    "rows": [c.record.row_number for c in (d.right if side_label == "right" else d.left)],
                },
            )
            for d in diffs
        ],
    )


def _near_boundary_hint(diffs: list[KeyDiff], side_label: str, other: Evaluation) -> FindingDraft | None:
    """Without a declared window, flag one-sided records just beyond the other side's observed time range."""
    times = [c.record.occurred_at for c in other.contributions if c.record.occurred_at is not None]
    if not times or not diffs:
        return None
    lo, hi = min(times), max(times)
    near = []
    for d in diffs:
        rows = d.right if side_label == "right" else d.left
        ts = [c.record.occurred_at for c in rows if c.record.occurred_at is not None]
        if ts and all((hi < t <= hi + CUTOFF_PROXIMITY) or (lo - CUTOFF_PROXIMITY <= t < lo) for t in ts):
            near.append(d)
    if not near:
        return None
    indicative = sum(((d.right_value if side_label == "right" else d.left_value) for d in near), ZERO)
    return FindingDraft(
        type=FindingType.DATE_CUTOFF,
        evidence_status=EvidenceStatus.SUSPECTED,
        title=f"{len(near)} {side_label}-only keys sit just outside the other side's time range",
        description=(
            f"These records fall within {int(CUTOFF_PROXIMITY.total_seconds() // 3600)} hours beyond the earliest or latest "
            f"record on the other side ({lo.isoformat()} to {hi.isoformat()}), which suggests a different cutoff or time zone. "
            f"Indicative amount {indicative}; not attributed because the other source declares no window."
        ),
        amount_impact=None,
        rule_id="data.near_cutoff",
        classification_reason="Timing pattern only; the other side's reporting window is not known.",
        calculation={
            "other_observed_range": [lo.isoformat(), hi.isoformat()],
            "indicative_amount": str(indicative),
            "keys": len(near),
        },
        evidence=[
            EvidenceDraft(
                Side(side_label),
                d.key,
                d.left_value if d.left else None,
                d.right_value if d.right else None,
                None,
                {},
            )
            for d in near
        ],
    )


def _refund_identity(diffs: list[KeyDiff]) -> list[KeyDiff]:
    matched = []
    for d in diffs:
        refunds = [sum(((c.record.refund_amount or ZERO) for c in rows), ZERO) for rows in (d.left, d.right)]
        if any(r > 0 and abs(d.mismatch_delta) == r for r in refunds):
            matched.append(d)
    return matched


def _refund_finding(diffs: list[KeyDiff]) -> FindingDraft:
    amount = sum((d.mismatch_delta for d in diffs), ZERO)
    return FindingDraft(
        type=FindingType.REFUND_POLICY,
        evidence_status=EvidenceStatus.CONFIRMED,
        title=f"Refunds explain the value difference on {len(diffs)} keys",
        description=(
            "For each listed key the two sides differ by exactly the refunded amount recorded for it, so one source "
            f"reports amounts net of refunds and the other gross. Effect on the difference: {amount}."
        ),
        amount_impact=amount,
        rule_id="data.refund_identity",
        classification_reason="Per-key arithmetic identity |right - left| = recorded refund, checked to the cent for every listed key.",
        assumptions=[
            "The identity is taken as evidence of different refund treatment in the sources; the metric definitions agree."
        ],
        calculation={
            "formula": "sum(right_k - left_k) where |right_k - left_k| == refund_k",
            "keys": len(diffs),
        },
        evidence=[
            EvidenceDraft(
                Side.BOTH,
                d.key,
                d.left_value,
                d.right_value,
                d.mismatch_delta,
                {"refund": [str(c.record.refund_amount) for c in d.left + d.right if c.record.refund_amount]},
            )
            for d in diffs
        ],
    )


# ---------------------------------------------------------------------------- driver


def run_autopsy(left: SideInput, right: SideInput) -> AutopsyResult:
    base_left = evaluate(left.records, left.spec)
    base_right = evaluate(right.records, right.spec)
    check_comparable(base_left, base_right)
    delta = base_right.value - base_left.value

    left_eval, _, replay, findings = _replay(left, right, base_left)
    right_eval = base_right

    for label, side in (("left", left), ("right", right)):
        current, other = (left_eval, right_eval) if label == "left" else (right_eval, left_eval)
        adjusted, finding = _amplification(label, side, current, other)
        if finding:
            findings.append(finding)
        if label == "left":
            left_eval = adjusted
        else:
            right_eval = adjusted

    rec = reconcile(left_eval, right_eval)
    refined: list[FindingDraft] = []
    for diffs_attr, side_label, other_label, other_side, other_eval in (
        ("missing", "left", "right", right, right_eval),
        ("extra", "right", "left", left, left_eval),
    ):
        diffs = getattr(rec, diffs_attr)
        window = _window(other_side.provenance)
        if window:
            cut = [d for d in diffs if _outside(d.left if side_label == "left" else d.right, window)]
            if cut:
                refined.append(_cutoff_finding(cut, side_label, other_label, window, other_side.provenance))
                cut_ids = {id(d) for d in cut}
                setattr(rec, diffs_attr, [d for d in diffs if id(d) not in cut_ids])
        else:
            hint = _near_boundary_hint(diffs, side_label, other_eval)
            if hint:
                refined.append(hint)
    refunds = _refund_identity(rec.mismatched)
    if refunds:
        refined.append(_refund_finding(refunds))
        refund_ids = {id(d) for d in refunds}
        rec.mismatched = [d for d in rec.mismatched if id(d) not in refund_ids]

    data_findings = build_findings(left_eval, right_eval, rec, left.keys, right.keys)
    findings.extend(refined)
    findings.extend(data_findings)

    attributed = sum((f.amount_impact for f in findings if f.amount_impact is not None), ZERO)
    if attributed != delta:  # each stage partitions what is left of delta; this guards future changes
        raise AssertionError(f"autopsy does not reconcile: attributed {attributed} != delta {delta}")

    for f in findings:
        f.evidence.sort(
            key=lambda e: (
                e.business_key is None,
                e.business_key or "",
                e.side.value,
                e.detail.get("row_number") or 0,
            )
        )
    order = 0
    for f in findings:
        if f.amount_impact is not None:
            order += 1
            f.step_order = order
        else:
            f.step_order = None
    return AutopsyResult(
        left_value=base_left.value,
        right_value=base_right.value,
        findings=findings,
        replay=replay,
        dna=compare(left.spec, right.spec),
        comparison=rec,
        left_final=left_eval,
        right_final=right_eval,
        base_left=base_left,
        base_right=base_right,
    )
