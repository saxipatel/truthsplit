"""What-if simulation: apply one supported correction to in-memory copies of an investigation's inputs and
re-run the Difference Autopsy. Nothing here touches the database; callers persist only the result.

Rules (parameters in parentheses):
  remove_duplicates  (side, kind)            drop exact duplicate rows, or join copies (same amount, different row)
  align_window       (side)                  give `side` the other side's date window, time zone and end bound
  refund_policy      (side, policy)          change `side`'s refund policy
  align_definition   (side)                  give `side` the other side's whole definition
  adjust_mapping     (side, mapping)         re-normalize a file dataset with another column mapping (records supplied by caller)
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.comparison.metrics import Evaluation, MetricEvaluationError, Record, unmapped_fields
from app.diagnosis.autopsy import AutopsyResult, SideInput, run_autopsy
from app.models.metrics import RefundPolicy
from app.normalization.mapping import ColumnMapping

ZERO = Decimal(0)
SideName = Literal["left", "right"]
WINDOW_FIELDS = ("window_start", "window_end", "window_end_inclusive", "timezone")


class RemoveDuplicates(BaseModel):
    rule_type: Literal["remove_duplicates"]
    side: Literal["left", "right", "both"] = "both"
    kind: Literal["exact", "join_copies"] = "exact"


class AlignWindow(BaseModel):
    rule_type: Literal["align_window"]
    side: SideName


class SetRefundPolicy(BaseModel):
    rule_type: Literal["refund_policy"]
    side: SideName
    policy: RefundPolicy


class AlignDefinition(BaseModel):
    rule_type: Literal["align_definition"]
    side: SideName


class AdjustMapping(BaseModel):
    rule_type: Literal["adjust_mapping"]
    side: SideName
    mapping: ColumnMapping


SimulationRequest = Annotated[
    RemoveDuplicates | AlignWindow | SetRefundPolicy | AlignDefinition | AdjustMapping,
    Field(discriminator="rule_type"),
]


class SimulationError(ValueError):
    pass


@dataclass
class SideChange:
    side: str
    records_before: int
    records_after: int
    changed_contributions: int  # records whose contribution differs, including ones added or dropped
    samples: list[dict]


@dataclass
class SimulationOutcome:
    baseline: AutopsyResult
    simulated: AutopsyResult
    changes: list[SideChange]

    @property
    def resolved_amount(self) -> Decimal:
        """How much closer to zero the difference is; negative if the change widens it."""
        return abs(self.baseline.delta) - abs(self.simulated.delta)

    @property
    def affected_rows(self) -> int:
        return sum(c.changed_contributions for c in self.changes)


def _exact_copies(records: list[Record]) -> set[int]:
    seen: set[tuple[str | None, str]] = set()
    dropped = set()
    for r in sorted(records, key=lambda r: r.row_number):
        key = (r.business_key, r.raw_payload_hash)
        if r.business_key is not None and key in seen:
            dropped.add(r.id)
        seen.add(key)
    return dropped


def _join_copies(records: list[Record], value_field: str) -> set[int]:
    groups: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.business_key is not None:
            groups[r.business_key].append(r)
    dropped = set()
    for rows in groups.values():
        rows.sort(key=lambda r: r.row_number)
        values = {getattr(r, value_field) for r in rows}
        if (
            len(rows) > 1
            and len(values) == 1
            and None not in values
            and len({r.raw_payload_hash for r in rows}) == len(rows)
        ):
            dropped |= {r.id for r in rows[1:]}
    return dropped


def apply_rule(
    rule, left: SideInput, right: SideInput, remapped: list[Record] | None = None
) -> tuple[SideInput, SideInput]:
    sides = {"left": left, "right": right}
    other = {"left": right, "right": left}
    targets = ["left", "right"] if getattr(rule, "side", None) == "both" else [rule.side]
    for name in targets:
        s = sides[name]
        if isinstance(rule, RemoveDuplicates):
            dropped = (
                _exact_copies(s.records)
                if rule.kind == "exact"
                else _join_copies(s.records, s.spec.value_field)
            )
            sides[name] = replace(s, records=[r for r in s.records if r.id not in dropped])
        elif isinstance(rule, AlignWindow):
            sides[name] = replace(
                s, spec=s.spec.model_copy(update={f: getattr(other[name].spec, f) for f in WINDOW_FIELDS})
            )
        elif isinstance(rule, SetRefundPolicy):
            sides[name] = replace(s, spec=s.spec.model_copy(update={"refund_policy": rule.policy}))
        elif isinstance(rule, AlignDefinition):
            sides[name] = replace(s, spec=other[name].spec.model_copy())
        elif isinstance(rule, AdjustMapping):
            if remapped is None:
                raise SimulationError("adjust_mapping needs the re-normalized records")
            sides[name] = replace(s, records=remapped, mapping=rule.mapping.model_dump(mode="json"))
    for name, s in sides.items():
        missing = unmapped_fields(s.spec, s.mapping)
        if missing:
            raise SimulationError(
                f"simulated {name} definition uses {', '.join(missing)}, which its data does not map"
            )
    return sides["left"], sides["right"]


def _changes(name: str, before: Evaluation, after: Evaluation, by_row: bool) -> SideChange:
    key = (lambda c: c.record.row_number) if by_row else (lambda c: c.record.id)
    b = {key(c): c for c in before.contributions}
    a = {key(c): c for c in after.contributions}
    changed = []
    for k in sorted(b.keys() | a.keys()):
        vb = b[k].value if k in b else None
        va = a[k].value if k in a else None
        if vb != va:
            r = (a.get(k) or b.get(k)).record
            changed.append(
                {
                    "row_number": r.row_number,
                    "business_key": r.business_key,
                    "before": str(vb) if vb is not None else None,
                    "after": str(va) if va is not None else None,
                }
            )
    return SideChange(name, len(before.contributions), len(after.contributions), len(changed), changed[:200])


def simulate(
    rule, left: SideInput, right: SideInput, remapped: list[Record] | None = None
) -> SimulationOutcome:
    baseline = run_autopsy(left, right)
    sim_left, sim_right = apply_rule(rule, left, right, remapped)
    try:
        simulated = run_autopsy(sim_left, sim_right)
    except MetricEvaluationError as exc:
        raise SimulationError(f"simulated inputs cannot be evaluated: {exc}") from exc
    remap = isinstance(rule, AdjustMapping)
    changes = [
        _changes("left", baseline.base_left, simulated.base_left, remap and rule.side == "left"),
        _changes("right", baseline.base_right, simulated.base_right, remap and rule.side == "right"),
    ]
    return SimulationOutcome(baseline, simulated, changes)
