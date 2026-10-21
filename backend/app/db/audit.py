from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    session: Session, action: str, target_type: str, target_id, actor: str | None = None, **metadata
) -> None:
    """Add an audit event to the current transaction; it commits with the change it describes."""
    session.add(
        AuditEvent(
            actor=actor or "local-user",
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            metadata_json=metadata,
        )
    )
