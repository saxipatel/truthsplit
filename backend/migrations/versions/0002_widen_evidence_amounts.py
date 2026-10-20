"""widen evidence amounts

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "evidence_records",
        "left_amount",
        existing_type=sa.NUMERIC(precision=18, scale=4),
        type_=sa.Numeric(precision=24, scale=4),
        existing_nullable=True,
    )
    op.alter_column(
        "evidence_records",
        "right_amount",
        existing_type=sa.NUMERIC(precision=18, scale=4),
        type_=sa.Numeric(precision=24, scale=4),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "evidence_records",
        "right_amount",
        existing_type=sa.Numeric(precision=24, scale=4),
        type_=sa.NUMERIC(precision=18, scale=4),
        existing_nullable=True,
    )
    op.alter_column(
        "evidence_records",
        "left_amount",
        existing_type=sa.Numeric(precision=24, scale=4),
        type_=sa.NUMERIC(precision=18, scale=4),
        existing_nullable=True,
    )
