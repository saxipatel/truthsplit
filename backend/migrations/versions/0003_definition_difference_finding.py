"""definition_difference finding type

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD = (
    "date_cutoff",
    "filter_difference",
    "refund_policy",
    "join_amplification",
    "duplicate_records",
    "missing_records",
    "extra_records",
    "amount_mismatch",
    "key_quality",
    "unexplained",
)
NEW = OLD[:8] + ("definition_difference",) + OLD[8:]


def _replace(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_findings_type"), "findings", type_="check")
    listed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(op.f("ck_findings_type"), "findings", f"type IN ({listed})")


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    op.execute("DELETE FROM findings WHERE type = 'definition_difference'")
    _replace(OLD)
