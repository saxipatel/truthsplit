"""Record-level reconciliation of two evaluated metrics.

With delta = right - left, and each metric equal to the sum of its contributions, grouping the
contributions by business key splits delta exactly:

    delta = Σ_keys (right_k - left_k) + (right_nokey - left_nokey)

Each key's difference is then assigned to exactly one category, or split between two for matched keys,
in this fixed order:

    extra        key contributes only on the right                       +right_k
    missing      key contributes only on the left                        -left_k
    duplicates   matched key: exact copies (same raw row hash) beyond     right_dup - left_dup
                 the first occurrence on each side
    mismatch     matched key: what is left after duplicates               (right_k - right_dup) - (left_k - left_dup)
    no key       rows without a business key cannot be matched           right_nokey - left_nokey

The categories partition delta, so they sum to it exactly in Decimal arithmetic. The assignment depends on
this order (a missing key that was also duplicated is reported as missing), and it describes *which records*
differ, not *why*.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from app.comparison.metrics import Contribution, Evaluation, Record
from app.models.metrics import Aggregation

ZERO = Decimal(0)


class ComparisonError(ValueError):
    pass


@dataclass
class KeyDiff:
    key: str
    left: list[Contribution]
    right: list[Contribution]
    left_value: Decimal
    right_value: Decimal
    left_duplicate: Decimal = ZERO
    right_duplicate: Decimal = ZERO

    @property
    def delta(self) -> Decimal:
        return self.right_value - self.left_value

    @property
    def duplicate_delta(self) -> Decimal:
        return self.right_duplicate - self.left_duplicate

    @property
    def mismatch_delta(self) -> Decimal:
        return self.delta - self.duplicate_delta


@dataclass
class Reconciliation:
    left_value: Decimal
    right_value: Decimal
    extra: list[KeyDiff] = field(default_factory=list)
    missing: list[KeyDiff] = field(default_factory=list)
    duplicates: list[KeyDiff] = field(default_factory=list)
    mismatched: list[KeyDiff] = field(default_factory=list)
    left_no_key: list[Contribution] = field(default_factory=list)
    right_no_key: list[Contribution] = field(default_factory=list)
    matched_keys: int = 0
    left_only_keys: int = 0
    right_only_keys: int = 0
    left_duplicate_keys: int = 0
    right_duplicate_keys: int = 0

    @property
    def delta(self) -> Decimal:
        return self.right_value - self.left_value

    def totals(self) -> dict[str, Decimal]:
        return {
            "extra_records": sum((k.right_value for k in self.extra), ZERO),
            "missing_records": -sum((k.left_value for k in self.missing), ZERO),
            "duplicate_records": sum((k.duplicate_delta for k in self.duplicates), ZERO),
            "amount_mismatch": sum((k.mismatch_delta for k in self.mismatched), ZERO),
            "no_key": sum((c.value for c in self.right_no_key), ZERO)
            - sum((c.value for c in self.left_no_key), ZERO),
        }


def _group(contributions: list[Contribution]) -> tuple[dict[str, list[Contribution]], list[Contribution]]:
    by_key: dict[str, list[Contribution]] = defaultdict(list)
    no_key = []
    for c in contributions:
        if c.record.business_key is None:
            no_key.append(c)
        else:
            by_key[c.record.business_key].append(c)
    return by_key, no_key


def _duplicate_excess(rows: list[Contribution]) -> Decimal:
    """Value carried by rows that repeat an earlier row of the same key byte-for-byte."""
    seen: set[str] = set()
    excess = ZERO
    for c in sorted(rows, key=lambda c: c.record.row_number):
        if c.record.raw_payload_hash in seen:
            excess += c.value
        seen.add(c.record.raw_payload_hash)
    return excess


def check_comparable(left: Evaluation, right: Evaluation) -> None:
    if left.spec.aggregation is not right.spec.aggregation:
        raise ComparisonError(
            f"cannot compare a {left.spec.aggregation.value} metric with a {right.spec.aggregation.value} metric"
        )
    if (
        left.spec.aggregation is Aggregation.SUM
        and left.currency
        and right.currency
        and left.currency != right.currency
    ):
        raise ComparisonError(f"metrics are in different currencies ({left.currency} vs {right.currency})")


def reconcile(left: Evaluation, right: Evaluation) -> Reconciliation:
    check_comparable(left, right)
    left_keys, left_no_key = _group(left.contributions)
    right_keys, right_no_key = _group(right.contributions)
    rec = Reconciliation(left.value, right.value, left_no_key=left_no_key, right_no_key=right_no_key)
    rec.left_duplicate_keys = sum(1 for rows in left_keys.values() if len(rows) > 1)
    rec.right_duplicate_keys = sum(1 for rows in right_keys.values() if len(rows) > 1)

    for key in sorted(left_keys.keys() | right_keys.keys()):
        lrows, rrows = left_keys.get(key, []), right_keys.get(key, [])
        diff = KeyDiff(
            key,
            lrows,
            rrows,
            sum((c.value for c in lrows), ZERO),
            sum((c.value for c in rrows), ZERO),
        )
        if not lrows:
            rec.right_only_keys += 1
            if diff.delta:
                rec.extra.append(diff)
            continue
        if not rrows:
            rec.left_only_keys += 1
            if diff.delta:
                rec.missing.append(diff)
            continue
        rec.matched_keys += 1
        diff.left_duplicate = _duplicate_excess(lrows)
        diff.right_duplicate = _duplicate_excess(rrows)
        if diff.duplicate_delta:
            rec.duplicates.append(diff)
        if diff.mismatch_delta:
            rec.mismatched.append(diff)

    explained = sum(rec.totals().values(), ZERO)
    if explained != rec.delta:  # holds by construction; guards against future edits breaking the partition
        raise AssertionError(f"reconciliation does not partition delta: {explained} != {rec.delta}")
    return rec


def exclusion_index(excluded: dict[str, list[Record]]) -> dict[str, list[str]]:
    """business_key -> reasons its rows were excluded by a definition."""
    index: dict[str, set[str]] = defaultdict(set)
    for reason, rows in excluded.items():
        for r in rows:
            if r.business_key is not None:
                index[r.business_key].add(reason)
    return {k: sorted(v) for k, v in index.items()}


def presence(key: str, dataset_keys: set[str], exclusions: dict[str, list[str]]) -> str:
    """Why a key contributes nothing on one side: absent from the data, or removed by that side's definition."""
    if key not in dataset_keys:
        return "absent"
    if key in exclusions:
        return "excluded:" + ",".join(exclusions[key])
    return "zero_value"
