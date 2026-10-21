"""Run and store what-if simulations. Only the `simulations` row (and an audit event) is written; the
investigation, its findings and every dataset stay exactly as they were."""

import uuid

from sqlalchemy.orm import Session

from app.adapters.files import read_table
from app.comparison.metrics import MetricSpec, Record
from app.core.config import Settings
from app.db import audit
from app.db.imports import upload_path
from app.db.investigations import InvestigationSide, _side_input, load_records
from app.diagnosis.autopsy import SideInput
from app.models import Investigation, Simulation
from app.models.investigations import SimulationRule
from app.normalization.mapping import normalize
from app.simulation.whatif import AdjustMapping, SimulationError, simulate


def investigation_inputs(session: Session, inv: Investigation) -> tuple[SideInput, SideInput]:
    by_side = {i.side.value: i for i in inv.inputs}
    sides = []
    for name, metric in (("left", inv.left_metric), ("right", inv.right_metric)):
        dataset = by_side[name].dataset
        if dataset.fingerprint != by_side[name].dataset_fingerprint:
            raise SimulationError(
                f"the {name} dataset no longer matches the fingerprint recorded with the investigation"
            )
        sides.append(
            _side_input(
                InvestigationSide(dataset, metric),
                MetricSpec.from_model(metric),
                load_records(session, dataset.id),
            )
        )
    return sides[0], sides[1]


def _remapped_records(
    inv: Investigation, rule: AdjustMapping, settings: Settings
) -> tuple[list[Record], dict]:
    dataset = next(i.dataset for i in inv.inputs if i.side.value == rule.side)
    if dataset.upload is None:
        raise SimulationError(
            "adjust_mapping is only available for file datasets; query snapshots keep their registered mapping"
        )
    path = upload_path(settings.storage_dir, dataset.upload)
    if not path.exists():
        raise SimulationError("the uploaded file for this dataset is missing from storage")
    encoding = (dataset.schema_json or {}).get("encoding") or "utf-8"
    table = read_table(
        path,
        dataset.upload.file_type,
        sheet=dataset.sheet_name,
        encoding=encoding,
        max_rows=settings.max_rows,
    )
    result = normalize(table, rule.mapping)
    if not result.records:
        first = result.issues[0] if result.issues else None
        detail = f" (row {first.row_number}, {first.column_name}: {first.message})" if first else ""
        raise SimulationError(f"the new mapping rejects every row of the file{detail}")
    stats = {
        "rows_read": len(table.rows),
        "rows_accepted": len(result.records),
        "rows_rejected": result.rejected_rows,
        "issues": [i.__dict__ for i in result.issues[:20]],
    }
    # Negative ids: these records exist only in memory and must never be confused with stored ones.
    records = [
        Record(
            -(i + 1),
            r.row_number,
            r.source_record_id,
            r.business_key,
            r.occurred_at,
            r.amount,
            r.currency,
            r.status,
            r.refund_amount,
            r.attributes,
            r.raw_payload_hash,
        )
        for i, r in enumerate(result.records)
    ]
    return records, stats


def _summary(result) -> dict:
    return {
        "left": str(result.left_value),
        "right": str(result.right_value),
        "delta": str(result.delta),
        "explained": str(result.explained),
        "unexplained": str(result.delta - result.explained),
        "findings": [
            {
                "type": f.type.value,
                "rule_id": f.rule_id,
                "status": f.evidence_status.value,
                "amount": str(f.amount_impact) if f.amount_impact is not None else None,
                "title": f.title,
            }
            for f in result.findings
        ],
        "excluded": {"left": result.base_left.excluded_counts, "right": result.base_right.excluded_counts},
    }


def run_simulation(session: Session, settings: Settings, inv: Investigation, rule) -> Simulation:
    left, right = investigation_inputs(session, inv)
    remapped, remap_stats = (
        _remapped_records(inv, rule, settings) if isinstance(rule, AdjustMapping) else (None, None)
    )
    outcome = simulate(rule, left, right, remapped)
    base, sim = outcome.baseline, outcome.simulated
    if (base.left_value, base.right_value) != (inv.baseline_left, inv.baseline_right):
        raise SimulationError(
            "re-evaluating the stored inputs no longer reproduces the investigation baseline"
        )
    simulation = Simulation(
        id=uuid.uuid4(),
        investigation_id=inv.id,
        rule_type=SimulationRule(rule.rule_type),
        parameters_json=rule.model_dump(mode="json"),
        before_left=base.left_value,
        before_right=base.right_value,
        before_delta=base.delta,
        after_left=sim.left_value,
        after_right=sim.right_value,
        after_delta=sim.delta,
        resolved_amount=outcome.resolved_amount,
        affected_rows=outcome.affected_rows,
        result_json={
            "delta_change": str(sim.delta - base.delta),
            "baseline": _summary(base),
            "simulated": _summary(sim),
            "sides": [c.__dict__ for c in outcome.changes],
            "replay": [s.as_json() for s in sim.replay],
            "remap": remap_stats,
        },
    )
    session.add(simulation)
    audit.record(
        session,
        "simulation.run",
        "investigation",
        inv.id,
        simulation_id=str(simulation.id),
        rule_type=rule.rule_type,
        after_delta=str(sim.delta),
    )
    session.commit()
    return simulation
