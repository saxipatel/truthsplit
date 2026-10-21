"""Investigation memory: status workflow, resolution notes, history, recurring patterns and similar cases.

Notes and status record human decisions. They never change a finding's evidence status, which stays what the
engine could prove.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import audit
from app.memory.similarity import Profile, rank
from app.models import AuditEvent, Finding, Investigation, ResolutionNote
from app.models.investigations import Decision, EvidenceStatus, InvestigationStatus

S = InvestigationStatus
TRANSITIONS = {
    S.OPEN: {S.IN_REVIEW, S.RESOLVED, S.ARCHIVED},
    S.IN_REVIEW: {S.OPEN, S.RESOLVED, S.ARCHIVED},
    S.RESOLVED: {S.OPEN, S.ARCHIVED},
    S.ARCHIVED: {S.OPEN},
}


class WorkflowError(ValueError):
    pass


def change_status(
    session: Session, inv: Investigation, new: InvestigationStatus, note: str | None, reviewer: str | None
) -> Investigation:
    old = inv.status
    if new == old:
        raise WorkflowError(f"investigation is already {old.value}")
    if new not in TRANSITIONS[old]:
        raise WorkflowError(f"cannot move from {old.value} to {new.value}")
    if new is S.RESOLVED and not (note and note.strip()):
        raise WorkflowError("resolving an investigation needs a note explaining the resolution")
    inv.status = new
    if note:
        session.add(
            ResolutionNote(
                investigation_id=inv.id, decision=Decision.COMMENT, note=note.strip(), reviewer=reviewer
            )
        )
    audit.record(
        session,
        "investigation.status_changed",
        "investigation",
        inv.id,
        actor=reviewer,
        from_status=old.value,
        to_status=new.value,
    )
    session.commit()
    return inv


def add_note(
    session: Session,
    inv: Investigation,
    decision: Decision,
    note: str,
    finding_id: uuid.UUID | None,
    reviewer: str | None,
) -> ResolutionNote:
    finding = None
    if finding_id is not None:
        finding = session.get(Finding, finding_id)
        if finding is None or finding.investigation_id != inv.id:
            raise WorkflowError("finding does not belong to this investigation")
    if decision is not Decision.COMMENT and finding is None:
        raise WorkflowError("accepting or rejecting needs the finding it applies to")
    entry = ResolutionNote(
        investigation_id=inv.id,
        finding_id=finding_id,
        decision=decision,
        note=note.strip(),
        reviewer=reviewer,
    )
    session.add(entry)
    session.flush()
    audit.record(
        session,
        "note.added",
        "investigation",
        inv.id,
        actor=reviewer,
        note_id=str(entry.id),
        decision=decision.value,
        finding_id=str(finding_id) if finding_id else None,
        finding_rule=finding.rule_id if finding else None,
    )
    session.commit()
    return entry


def history(session: Session, inv: Investigation) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_type == "investigation", AuditEvent.target_id == str(inv.id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )


def profile(inv: Investigation) -> Profile:
    p = Profile(str(inv.id), inv.title)
    for f in inv.findings:
        if f.evidence_status is EvidenceStatus.CONFIRMED:
            p.confirmed_rules.add(f.rule_id)
        elif f.evidence_status is EvidenceStatus.SUSPECTED:
            p.suspected_rules.add(f.rule_id)
    p.metrics = {inv.left_metric.name, inv.right_metric.name}
    for i in inv.inputs:
        d = i.dataset
        if d.query_key:
            p.sources.add(f"registered query {d.query_key}")
        elif d.upload is not None:
            p.sources.add(f"file {d.upload.original_filename} (sha256 {d.upload.sha256[:12]})")
    dna = (inv.config_json or {}).get("metric_dna", {})
    p.dna_differences = {f["field"] for f in dna.get("fields", []) if f.get("differs")}
    return p


def similar(session: Session, inv: Investigation, limit: int = 10) -> list[dict]:
    others = session.scalars(select(Investigation).where(Investigation.id != inv.id)).all()
    target = profile(inv)
    matches = rank(target, [profile(o) for o in others], limit)
    rules_here = {f.rule_id for f in inv.findings}
    out = []
    for m in matches:
        accepted = session.execute(
            select(ResolutionNote, Finding)
            .join(Finding, Finding.id == ResolutionNote.finding_id)
            .where(
                ResolutionNote.investigation_id == uuid.UUID(m.investigation_id),
                ResolutionNote.decision == Decision.ACCEPTED,
            )
            .order_by(ResolutionNote.reviewed_at)
        ).all()
        suggestions = [
            {
                "rule_id": f.rule_id,
                "finding_title": f.title,
                "note": n.note,
                "reviewer": n.reviewer,
                "reviewed_at": n.reviewed_at.isoformat(),
                "applies_here": f.rule_id in rules_here,
            }
            for n, f in accepted
        ]
        out.append(
            {
                "investigation_id": m.investigation_id,
                "title": m.title,
                "score": m.score,
                "reasons": m.reasons,
                "accepted_diagnoses": suggestions,
            }
        )
    return out


def patterns(session: Session, include_demo: bool = True) -> list[dict]:
    """Recurring discrepancy categories across stored investigations."""
    stmt = select(Finding, Investigation).join(Investigation, Investigation.id == Finding.investigation_id)
    if not include_demo:
        stmt = stmt.where(Investigation.is_demo.is_(False))
    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "investigations": set(),
            "confirmed": 0,
            "suspected": 0,
            "unexplained": 0,
            "attributed": Decimal(0),
            "last_seen": None,
        }
    )
    for f, inv in session.execute(stmt):
        a = agg[(f.type.value, f.rule_id)]
        a["investigations"].add(inv.id)
        a[f.evidence_status.value] += 1
        if f.amount_impact is not None:
            a["attributed"] += abs(f.amount_impact)
        a["last_seen"] = max(filter(None, [a["last_seen"], inv.created_at]))
    rows = [
        {
            "finding_type": t,
            "rule_id": r,
            "investigations": len(a["investigations"]),
            "recurring": len(a["investigations"]) > 1,
            "confirmed": a["confirmed"],
            "suspected": a["suspected"],
            "unexplained": a["unexplained"],
            "total_absolute_amount": str(a["attributed"]),
            "last_seen": a["last_seen"].isoformat() if a["last_seen"] else None,
        }
        for (t, r), a in agg.items()
    ]
    return sorted(rows, key=lambda x: (-x["investigations"], x["finding_type"], x["rule_id"]))
