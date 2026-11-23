"""Durable jobs for the opt-in managed Rails pilot."""

import sqlalchemy as sa
from alembic import op

revision = "0dbc73f7e891"
down_revision = "e4b92f7a6103"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rails_provision_jobs",
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), primary_key=True),
        sa.Column("source_sha", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Preserve provisioning history and bound Secret ownership during rollback")
