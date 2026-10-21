import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.comparison.metrics import MetricEvaluationError, MetricSpec, Record, unmapped_fields
from app.comparison.sql import metric_sql, query_hash
from app.db import audit
from app.diagnosis.autopsy import SideInput, run_autopsy
from app.diagnosis.dna import REPLAY_STEPS, source_facts
from app.models import (
    ComparisonResult,
    Dataset,
    DatasetRecord,
    EvidenceRecord,
    Finding,
    Investigation,
    InvestigationInput,
    MetricDefinition,
)
from app.models.investigations import Side

log = logging.getLogger(__name__)

ENGINE_VERSION = "2"


def load_records(session: Session, dataset_id: uuid.UUID) -> list[Record]:
    rows = session.execute(
        select(
            DatasetRecord.id,
            DatasetRecord.row_number,
            DatasetRecord.source_record_id,
            DatasetRecord.business_key,
            DatasetRecord.occurred_at,
            DatasetRecord.amount,
            DatasetRecord.currency,
            DatasetRecord.status,
            DatasetRecord.refund_amount,
            DatasetRecord.attributes,
            DatasetRecord.raw_payload_hash,
        )
        .where(DatasetRecord.dataset_id == dataset_id)
        .order_by(DatasetRecord.row_number)
    )
    return [Record(*row) for row in rows]


def create_metric_definition(
    session: Session, name: str, description: str | None, spec: MetricSpec
) -> MetricDefinition:
    """Insert the next version of `name`. Existing versions are never modified."""
    # Serialise concurrent version bumps of the same name; the unique constraint is the backstop.
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(name))))
    latest = session.scalar(select(func.max(MetricDefinition.version)).where(MetricDefinition.name == name))
    definition = MetricDefinition(
        name=name,
        version=(latest or 0) + 1,
        description=description,
        date_field="occurred_at",
        **spec.model_dump(mode="python", exclude={"filters"}),
        filters=[f.model_dump(mode="json") for f in spec.filters],
    )
    session.add(definition)
    session.commit()
    return definition


@dataclass
class InvestigationSide:
    dataset: Dataset
    metric: MetricDefinition


def _side_input(side: InvestigationSide, spec: MetricSpec, records: list[Record]) -> SideInput:
    return SideInput(
        records=records,
        spec=spec,
        mapping=side.dataset.column_mapping,
        source_type=side.dataset.source_type.value,
        provenance=side.dataset.schema_json or {},
        label=side.dataset.label,
    )


def run_investigation(
    session: Session, title: str, left: InvestigationSide, right: InvestigationSide, *, is_demo: bool = False
) -> Investigation:
    """Evaluate both metrics, reconcile them record by record and persist the result in one transaction.
    Raises MetricEvaluationError / ComparisonError (both ValueError) when the inputs cannot be compared."""
    left_spec, right_spec = MetricSpec.from_model(left.metric), MetricSpec.from_model(right.metric)
    for label, side, spec in (("left", left, left_spec), ("right", right, right_spec)):
        missing = unmapped_fields(spec, side.dataset.column_mapping)
        if missing:
            raise MetricEvaluationError(
                f"{label} metric '{side.metric.name}' v{side.metric.version} uses {', '.join(missing)}, "
                f"which dataset '{side.dataset.label}' does not map"
            )
    left_records = load_records(session, left.dataset.id)
    right_records = load_records(session, right.dataset.id)
    result = run_autopsy(
        _side_input(left, left_spec, left_records), _side_input(right, right_spec, right_records)
    )
    rec, drafts = result.comparison, result.findings
    explained = result.explained
    unexplained = result.delta - explained
    proof = {}
    for side, spec in (("left", left_spec), ("right", right_spec)):
        sql, params = metric_sql(spec)
        proof[side] = {
            "sql": sql,
            "params": {k: str(v) for k, v in params.items()},
            "query_hash": query_hash(sql, params),
        }
    dna = result.dna | {"sources": {"left": source_facts(left.dataset), "right": source_facts(right.dataset)}}

    investigation = Investigation(
        id=uuid.uuid4(),
        title=title,
        left_metric_id=left.metric.id,
        right_metric_id=right.metric.id,
        baseline_left=result.left_value,
        baseline_right=result.right_value,
        baseline_delta=result.delta,
        explained_delta=explained,
        unexplained_delta=unexplained,
        config_json={
            "key_field": "business_key",
            "delta": "right - left",
            "attribution_order": [
                *(step for step, _, _ in REPLAY_STEPS),
                "join_amplification",
                "query_window_cutoff",
                "refund_identity",
                "missing_records",
                "extra_records",
                "duplicate_records",
                "amount_mismatch",
                "no_key",
            ],
            "excluded": {
                "left": result.base_left.excluded_counts,
                "right": result.base_right.excluded_counts,
            },
            "currency": result.base_left.currency or result.base_right.currency,
            "metric_dna": dna,
            "replay": [step.as_json() for step in result.replay],
            "proof_queries": proof,
        },
        engine_version=ENGINE_VERSION,
        is_demo=is_demo,
    )
    session.add(investigation)
    session.flush()  # child rows below reference it without an ORM relationship
    for side, s, records in ((Side.LEFT, left, left_records), (Side.RIGHT, right, right_records)):
        session.add(
            InvestigationInput(
                investigation_id=investigation.id,
                side=side,
                dataset_id=s.dataset.id,
                dataset_fingerprint=s.dataset.fingerprint,
                row_count=len(records),
            )
        )
    session.add(
        ComparisonResult(
            investigation_id=investigation.id,
            key_field="business_key",
            matched_keys=rec.matched_keys,
            left_only_keys=rec.left_only_keys,
            right_only_keys=rec.right_only_keys,
            left_duplicate_keys=rec.left_duplicate_keys,
            right_duplicate_keys=rec.right_duplicate_keys,
            left_null_keys=len(rec.left_no_key),
            right_null_keys=len(rec.right_no_key),
            mismatched_keys=len(rec.mismatched),
            summary_json={
                "left_contributing_rows": len(result.left_final.contributions),
                "right_contributing_rows": len(result.right_final.contributions),
                "totals": {k: str(v) for k, v in rec.totals().items()},
            },
        )
    )
    session.flush()

    evidence_rows = []
    for d in drafts:
        finding = Finding(
            id=uuid.uuid4(),
            investigation_id=investigation.id,
            step_order=d.step_order,
            type=d.type,
            evidence_status=d.evidence_status,
            title=d.title,
            description=d.description,
            amount_impact=d.amount_impact,
            rule_id=d.rule_id,
            classification_reason=d.classification_reason,
            assumptions_json=d.assumptions,
            calculation_json=d.calculation,
            proof_hash=d.proof_hash,
            affected_row_count=sum(_rows_in(e) for e in d.evidence),
            overlap_note=d.overlap_note,
        )
        session.add(finding)
        evidence_rows.extend(
            {
                "finding_id": finding.id,
                "side": e.side,
                "dataset_record_id": e.dataset_record_id,
                "source_record_id": e.source_record_id,
                "business_key": e.business_key,
                "left_amount": e.left_amount,
                "right_amount": e.right_amount,
                "contribution": e.contribution,
                "detail": e.detail,
            }
            for e in d.evidence
        )
    session.flush()
    if evidence_rows:
        session.execute(insert(EvidenceRecord), evidence_rows)
    audit.record(
        session,
        "investigation.created",
        "investigation",
        investigation.id,
        delta=str(result.delta),
        unexplained=str(unexplained),
        findings=len(drafts),
        engine_version=ENGINE_VERSION,
    )
    session.commit()
    session.expire_all()  # callers read back what was stored, at the stored numeric scale
    log.info(
        "investigation created",
        extra={"investigation_id": str(investigation.id), "delta": str(rec.delta), "findings": len(drafts)},
    )
    return investigation


def _rows_in(evidence) -> int:
    """Per-key evidence lists its rows; per-row evidence (rows without a key) is one row."""
    listed = len(evidence.detail.get("left_rows", [])) + len(evidence.detail.get("right_rows", []))
    return listed or 1
