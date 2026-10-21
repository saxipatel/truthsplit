"""Backfill investigation.created audit events for investigations created before auditing existed

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Marked as backfilled so they are never mistaken for events recorded at the time.
    op.execute(
        """
        INSERT INTO audit_events (actor, action, target_type, target_id, occurred_at, metadata_json)
        SELECT 'system', 'investigation.created', 'investigation', i.id::text, i.created_at,
               jsonb_build_object('backfilled', true, 'delta', i.baseline_delta::text, 'engine_version', i.engine_version)
        FROM investigations i
        WHERE NOT EXISTS (
            SELECT 1 FROM audit_events e
            WHERE e.target_type = 'investigation' AND e.target_id = i.id::text AND e.action = 'investigation.created'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM audit_events WHERE action = 'investigation.created' AND metadata_json ->> 'backfilled' = 'true'"
    )
