"""phase2 repository analysis and evidence"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "9f62b89f6b11"
down_revision = "637f876305a8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_runs", sa.Column("commit_sha", sa.String(length=64), nullable=True))
    op.add_column("analysis_runs", sa.Column("analysis_mode", sa.String(length=32), nullable=False, server_default="fake"))
    op.add_column("analysis_runs", sa.Column("incomplete_reason", sa.String(length=64), nullable=True))
    op.add_column("analysis_runs", sa.Column("reproducibility_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analysis_runs", sa.Column("attempt_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("excerpt_hash", sa.String(length=64), nullable=False),
        sa.Column("detector", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evidence_items_run_id"), "evidence_items", ["run_id"], unique=False)
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_organization_id"), "audit_events", ["organization_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_audit_events_organization_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_evidence_items_run_id"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_column("analysis_runs", "attempt_deadline_at")
    op.drop_column("analysis_runs", "reproducibility_json")
    op.drop_column("analysis_runs", "incomplete_reason")
    op.drop_column("analysis_runs", "analysis_mode")
    op.drop_column("analysis_runs", "commit_sha")
