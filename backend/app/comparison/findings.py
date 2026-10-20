"""Turn a reconciliation into findings with evidence.

These are *data-level* findings: they identify the records that account for each part of the difference.
They do not determine why those records differ; root-cause rules (refund policy, join amplification,
date cutoff) run in the Difference Autopsy. Classification rules:

  confirmed    the amount is recomputed exactly from the listed records (missing, extra, duplicate,
               mismatched keys)
  suspected    a data-quality signal without a quantified attribution (duplicate or missing keys)
  unexplained  part of the difference that the available data cannot attribute (rows without a key)
"""

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from app.comparison.matching import KeyDiff, Reconciliation, exclusion_index, presence
from app.comparison.metrics import Contribution, Evaluation
from app.models.investigations import EvidenceStatus, FindingType, Side

ZERO = Decimal(0)


@dataclass
class EvidenceDraft:
    side: Side
    business_key: str | None
    left_amount: Decimal | None
    right_amount: Decimal | None
    contribution: Decimal | None
    detail: dict
    dataset_record_id: int | None = None
    source_record_id: str | None = None


@dataclass
class FindingDraft:
    type: FindingType
    evidence_status: EvidenceStatus
    title: str
    description: str
    amount_impact: Decimal | None
    rule_id: str
    classification_reason: str
    step_order: int | None = None
    assumptions: list[str] = field(default_factory=list)
    calculation: dict = field(default_factory=dict)
    overlap_note: str | None = None
    evidence: list[EvidenceDraft] = field(default_factory=list)

    @property
    def proof_hash(self) -> str:
        payload = {
            "type": self.type.value,
            "amount": str(self.amount_impact),
            "calculation": self.calculation,
            "evidence": [
                [
                    e.side.value,
                    e.business_key,
                    str(e.contribution),
                    e.detail.get("left_rows"),
                    e.detail.get("right_rows"),
                ]
                for e in self.evidence
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _rows(contributions: list[Contribution]) -> list[dict]:
    return [
        {
            "row_number": c.record.row_number,
            "record_id": c.record.id,
            "source_record_id": c.record.source_record_id,
            "value": str(c.value),
        }
        for c in contributions
    ]


def _key_evidence(diff: KeyDiff, contribution: Decimal, side: Side, **detail) -> EvidenceDraft:
    return EvidenceDraft(
        side=side,
        business_key=diff.key,
        left_amount=diff.left_value if diff.left else None,
        right_amount=diff.right_value if diff.right else None,
        contribution=contribution,
        detail={"left_rows": _rows(diff.left), "right_rows": _rows(diff.right), **detail},
    )


DATA_LEVEL_REASON = (
    "Amount recomputed exactly from the listed records. This identifies which records differ; it does not "
    "establish why (for example a load failure versus a deliberate definition difference)."
)


def build_findings(
    left: Evaluation,
    right: Evaluation,
    rec: Reconciliation,
    left_dataset_keys: set[str],
    right_dataset_keys: set[str],
) -> list[FindingDraft]:
    totals = rec.totals()
    findings: list[FindingDraft] = []
    right_excl = exclusion_index(right.excluded)
    left_excl = exclusion_index(left.excluded)

    def one_sided(
        diffs: list[KeyDiff],
        ftype: FindingType,
        here: str,
        there: str,
        there_keys,
        there_excl,
        sign: int,
        rule: str,
    ):
        if not diffs:
            return
        evidence = []
        by_definition = 0
        for d in diffs:
            status = presence(d.key, there_keys, there_excl)
            by_definition += status.startswith("excluded")
            value = d.right_value if sign > 0 else -d.left_value
            evidence.append(_key_evidence(d, value, Side.RIGHT if sign > 0 else Side.LEFT, other_side=status))
        amount = totals[ftype.value]
        note = None
        if by_definition:
            note = (
                f"{by_definition} of these {len(diffs)} keys exist in the {there} dataset but were excluded by the "
                f"{there} metric definition; that part is a definition difference rather than missing data."
            )
        findings.append(
            FindingDraft(
                type=ftype,
                evidence_status=EvidenceStatus.CONFIRMED,
                title=f"{len(diffs)} keys contribute only on the {here}",
                description=(
                    f"{len(diffs)} business keys contribute to the {here} metric and not to the {there} metric, "
                    f"changing the difference by {amount}."
                ),
                amount_impact=amount,
                rule_id=rule,
                classification_reason=DATA_LEVEL_REASON,
                calculation={
                    "formula": f"{'+' if sign > 0 else '-'}sum({here} value of each key)",
                    "keys": len(diffs),
                },
                overlap_note=note,
                evidence=evidence,
            )
        )

    one_sided(
        rec.missing,
        FindingType.MISSING_RECORDS,
        "left",
        "right",
        right_dataset_keys,
        right_excl,
        -1,
        "data.missing_keys",
    )
    one_sided(
        rec.extra,
        FindingType.EXTRA_RECORDS,
        "right",
        "left",
        left_dataset_keys,
        left_excl,
        +1,
        "data.extra_keys",
    )

    if rec.duplicates:
        findings.append(
            FindingDraft(
                type=FindingType.DUPLICATE_RECORDS,
                evidence_status=EvidenceStatus.CONFIRMED,
                title=f"Exact duplicate rows on {len(rec.duplicates)} matched keys",
                description=(
                    "Rows identical in every source column to an earlier row of the same key. Their combined "
                    f"effect on the difference is {totals['duplicate_records']}."
                ),
                amount_impact=totals["duplicate_records"],
                rule_id="data.exact_duplicates",
                classification_reason=DATA_LEVEL_REASON
                + " Duplicates are rows with an identical raw row hash.",
                calculation={
                    "formula": "sum(right duplicate copies) - sum(left duplicate copies)",
                    "keys": len(rec.duplicates),
                },
                evidence=[
                    _key_evidence(
                        d,
                        d.duplicate_delta,
                        Side.BOTH,
                        left_duplicate=str(d.left_duplicate),
                        right_duplicate=str(d.right_duplicate),
                    )
                    for d in rec.duplicates
                ],
            )
        )

    if rec.mismatched:
        findings.append(
            FindingDraft(
                type=FindingType.AMOUNT_MISMATCH,
                evidence_status=EvidenceStatus.CONFIRMED,
                title=f"Different values for {len(rec.mismatched)} matched keys",
                description=(
                    "Keys present on both sides whose contributions differ (after exact duplicates are set aside). "
                    f"Combined effect: {totals['amount_mismatch']}."
                ),
                amount_impact=totals["amount_mismatch"],
                rule_id="data.value_mismatch",
                classification_reason=DATA_LEVEL_REASON,
                calculation={
                    "formula": "sum((right_k - right_dup_k) - (left_k - left_dup_k))",
                    "keys": len(rec.mismatched),
                },
                assumptions=[
                    "Keys are matched on business_key only; rows within a key are not paired individually."
                ],
                evidence=[_key_evidence(d, d.mismatch_delta, Side.BOTH) for d in rec.mismatched],
            )
        )

    if rec.left_no_key or rec.right_no_key:
        findings.append(
            FindingDraft(
                type=FindingType.UNEXPLAINED,
                evidence_status=EvidenceStatus.UNEXPLAINED,
                title="Rows without a business key",
                description=(
                    f"{len(rec.left_no_key)} left and {len(rec.right_no_key)} right contributing rows have no business "
                    f"key and cannot be matched. Their net effect on the difference is {totals['no_key']}."
                ),
                amount_impact=totals["no_key"],
                rule_id="data.unmatchable_rows",
                classification_reason="The input data lacks the key needed to attribute these rows.",
                calculation={"formula": "sum(right rows without key) - sum(left rows without key)"},
                evidence=[
                    EvidenceDraft(
                        Side.LEFT,
                        None,
                        c.value,
                        None,
                        -c.value,
                        {"row_number": c.record.row_number},
                        c.record.id,
                        c.record.source_record_id,
                    )
                    for c in rec.left_no_key
                ]
                + [
                    EvidenceDraft(
                        Side.RIGHT,
                        None,
                        None,
                        c.value,
                        c.value,
                        {"row_number": c.record.row_number},
                        c.record.id,
                        c.record.source_record_id,
                    )
                    for c in rec.right_no_key
                ],
            )
        )

    dup_keys = {"left": rec.left_duplicate_keys, "right": rec.right_duplicate_keys}
    if any(dup_keys.values()):
        findings.append(
            FindingDraft(
                type=FindingType.KEY_QUALITY,
                evidence_status=EvidenceStatus.SUSPECTED,
                title="Business keys are not unique",
                description=(
                    f"{dup_keys['left']} left and {dup_keys['right']} right keys have more than one contributing row. "
                    "Rows within a key are compared by total, not one-to-one."
                ),
                amount_impact=None,
                rule_id="data.non_unique_keys",
                classification_reason=(
                    "A data-quality signal, not an attributed amount: repeated keys can be legitimate (several lines "
                    "per order) or a sign of join amplification."
                ),
                calculation=dup_keys,
            )
        )

    for f in findings:
        f.evidence.sort(key=lambda e: (e.business_key is None, e.business_key or "", e.side.value))
    for i, f in enumerate(x for x in findings if x.amount_impact is not None):
        f.step_order = i + 1
    return findings
