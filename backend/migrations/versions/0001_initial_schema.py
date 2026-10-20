"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False)
    op.create_index(
        op.f("ix_audit_events_target_type_target_id"),
        "audit_events",
        ["target_type", "target_id"],
        unique=False,
    )
    op.create_table(
        "file_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "file_type",
            sa.Enum(
                "csv", "xlsx", "xls", name="file_type", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("sheet_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_uploads")),
        sa.UniqueConstraint("sha256", name=op.f("uq_file_uploads_sha256")),
    )
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "aggregation",
            sa.Enum(
                "sum",
                "count",
                "count_distinct",
                name="aggregation",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("value_field", sa.String(length=64), nullable=False),
        sa.Column(
            "grain",
            sa.Enum(
                "record", "business_key", name="grain", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("date_field", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=True),
        sa.Column("window_end", sa.DateTime(), nullable=True),
        sa.Column("window_end_inclusive", sa.Boolean(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "refund_policy",
            sa.Enum(
                "gross",
                "net_of_refunds",
                "exclude_refunded",
                name="refund_policy",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "null_policy",
            sa.Enum(
                "exclude", "reject", name="null_policy", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_metric_definitions_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_definitions")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_metric_definitions_name_version")),
    )
    op.create_table(
        "source_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("config_reference", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_connections")),
        sa.UniqueConstraint("label", name=op.f("uq_source_connections_label")),
    )
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "csv",
                "xlsx",
                "xls",
                "postgres_query",
                name="source_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("upload_id", sa.Uuid(), nullable=True),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("source_connection_id", sa.Uuid(), nullable=True),
        sa.Column("query_key", sa.String(length=120), nullable=True),
        sa.Column("query_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("query_hash", sa.String(length=64), nullable=True),
        sa.Column("column_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rejected_row_count", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(source_type = 'postgres_query') = (source_connection_id IS NOT NULL AND query_hash IS NOT NULL)",
            name=op.f("ck_datasets_query_provenance"),
        ),
        sa.CheckConstraint(
            "(source_type = 'postgres_query') OR upload_id IS NOT NULL",
            name=op.f("ck_datasets_file_provenance"),
        ),
        sa.ForeignKeyConstraint(
            ["source_connection_id"],
            ["source_connections.id"],
            name=op.f("fk_datasets_source_connection_id_source_connections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["file_uploads.id"],
            name=op.f("fk_datasets_upload_id_file_uploads"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_datasets_fingerprint")),
    )
    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("left_metric_id", sa.Uuid(), nullable=False),
        sa.Column("right_metric_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_review",
                "resolved",
                "archived",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("baseline_left", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("baseline_right", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("baseline_delta", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("explained_delta", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("unexplained_delta", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["left_metric_id"],
            ["metric_definitions.id"],
            name=op.f("fk_investigations_left_metric_id_metric_definitions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["right_metric_id"],
            ["metric_definitions.id"],
            name=op.f("fk_investigations_right_metric_id_metric_definitions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigations")),
    )
    op.create_index(op.f("ix_investigations_created_at"), "investigations", ["created_at"], unique=False)
    op.create_table(
        "comparison_results",
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("key_field", sa.String(length=64), nullable=False),
        sa.Column("matched_keys", sa.Integer(), nullable=False),
        sa.Column("left_only_keys", sa.Integer(), nullable=False),
        sa.Column("right_only_keys", sa.Integer(), nullable=False),
        sa.Column("left_duplicate_keys", sa.Integer(), nullable=False),
        sa.Column("right_duplicate_keys", sa.Integer(), nullable=False),
        sa.Column("left_null_keys", sa.Integer(), nullable=False),
        sa.Column("right_null_keys", sa.Integer(), nullable=False),
        sa.Column("mismatched_keys", sa.Integer(), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_comparison_results_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("investigation_id", name=op.f("pk_comparison_results")),
    )
    op.create_table(
        "dataset_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("business_key", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("refund_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_records_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_records")),
        sa.UniqueConstraint(
            "dataset_id", "row_number", name=op.f("uq_dataset_records_dataset_id_row_number")
        ),
    )
    op.create_index(
        op.f("ix_dataset_records_dataset_id_business_key"),
        "dataset_records",
        ["dataset_id", "business_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dataset_records_dataset_id_occurred_at"),
        "dataset_records",
        ["dataset_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
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
                name="type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "evidence_status",
            sa.Enum(
                "confirmed",
                "suspected",
                "unexplained",
                name="evidence_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount_impact", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("classification_reason", sa.Text(), nullable=False),
        sa.Column("assumptions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proof_hash", sa.String(length=64), nullable=True),
        sa.Column("affected_row_count", sa.Integer(), nullable=False),
        sa.Column("overlap_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_findings_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    op.create_index(
        op.f("ix_findings_investigation_id_type"), "findings", ["investigation_id", "type"], unique=False
    )
    op.create_table(
        "import_issues",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("column_name", sa.String(length=255), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_import_issues_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_issues")),
    )
    op.create_index(op.f("ix_import_issues_dataset_id"), "import_issues", ["dataset_id"], unique=False)
    op.create_table(
        "investigation_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "side",
            sa.Enum(
                "left", "right", "both", name="side", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("side IN ('left', 'right')", name=op.f("ck_investigation_inputs_side_not_both")),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_investigation_inputs_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_investigation_inputs_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_inputs")),
        sa.UniqueConstraint(
            "investigation_id", "side", name=op.f("uq_investigation_inputs_investigation_id_side")
        ),
    )
    op.create_table(
        "simulations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "rule_type",
            sa.Enum(
                "remove_duplicates",
                "align_window",
                "refund_policy",
                "align_definition",
                "adjust_mapping",
                name="rule_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("parameters_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("before_left", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("before_right", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("before_delta", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("after_left", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("after_right", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("after_delta", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("resolved_amount", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("affected_rows", sa.Integer(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_simulations_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulations")),
    )
    op.create_index(
        op.f("ix_simulations_investigation_id"), "simulations", ["investigation_id"], unique=False
    )
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column(
            "side",
            sa.Enum(
                "left", "right", "both", name="side", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("dataset_record_id", sa.BigInteger(), nullable=True),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("business_key", sa.String(length=255), nullable=True),
        sa.Column("left_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("right_amount", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("contribution", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_record_id"],
            ["dataset_records.id"],
            name=op.f("fk_evidence_records_dataset_record_id_dataset_records"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_evidence_records_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_records")),
    )
    op.create_index(op.f("ix_evidence_records_finding_id"), "evidence_records", ["finding_id"], unique=False)
    op.create_table(
        "resolution_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "accepted",
                "rejected",
                "comment",
                name="decision",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_resolution_notes_finding_id_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_resolution_notes_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_notes")),
    )
    op.create_index(
        op.f("ix_resolution_notes_investigation_id"), "resolution_notes", ["investigation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_resolution_notes_investigation_id"), table_name="resolution_notes")
    op.drop_table("resolution_notes")
    op.drop_index(op.f("ix_evidence_records_finding_id"), table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_index(op.f("ix_simulations_investigation_id"), table_name="simulations")
    op.drop_table("simulations")
    op.drop_table("investigation_inputs")
    op.drop_index(op.f("ix_import_issues_dataset_id"), table_name="import_issues")
    op.drop_table("import_issues")
    op.drop_index(op.f("ix_findings_investigation_id_type"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_dataset_records_dataset_id_occurred_at"), table_name="dataset_records")
    op.drop_index(op.f("ix_dataset_records_dataset_id_business_key"), table_name="dataset_records")
    op.drop_table("dataset_records")
    op.drop_table("comparison_results")
    op.drop_index(op.f("ix_investigations_created_at"), table_name="investigations")
    op.drop_table("investigations")
    op.drop_table("datasets")
    op.drop_table("source_connections")
    op.drop_table("metric_definitions")
    op.drop_table("file_uploads")
    op.drop_index(op.f("ix_audit_events_target_type_target_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_table("audit_events")
