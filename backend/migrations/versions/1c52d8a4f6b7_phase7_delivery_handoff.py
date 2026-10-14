"""Durable release preparation and deployment preflight records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "1c52d8a4f6b7"
down_revision = "63b1c9e4a205"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("release_specifications.id"), nullable=False),
        sa.Column("verified_build_id", sa.Uuid(), sa.ForeignKey("verified_builds.id"), nullable=False),
        sa.Column("environment_profile_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("release_id", name="uq_deployment_release"),
    )
    op.create_index("ix_deployment_runs_application_id", "deployment_runs", ["application_id"])
    op.create_index("ix_deployment_runs_status", "deployment_runs", ["status"])
    op.create_index(
        "uq_active_deployment_application_environment", "deployment_runs",
        ["application_id", "environment_profile_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor')"),
    )
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deployment_run_id", sa.Uuid(), sa.ForeignKey("deployment_runs.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("deployment_run_id", "sequence", name="uq_deployment_event_sequence"),
    )
    op.create_index("ix_deployment_events_deployment_run_id", "deployment_events", ["deployment_run_id"])
    op.create_table(
        "delivery_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("verified_build_id", sa.Uuid(), sa.ForeignKey("verified_builds.id"), nullable=False),
        sa.Column("deployment_run_id", sa.Uuid(), sa.ForeignKey("deployment_runs.id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("kind", "verified_build_id", name="uq_delivery_job_build_kind"),
    )
    op.create_index("ix_delivery_jobs_verified_build_id", "delivery_jobs", ["verified_build_id"])
    op.create_index("ix_delivery_jobs_status", "delivery_jobs", ["status"])


def downgrade():
    op.drop_index("ix_delivery_jobs_status", table_name="delivery_jobs")
    op.drop_index("ix_delivery_jobs_verified_build_id", table_name="delivery_jobs")
    op.drop_table("delivery_jobs")
    op.drop_index("ix_deployment_events_deployment_run_id", table_name="deployment_events")
    op.drop_table("deployment_events")
    op.drop_index("uq_active_deployment_application_environment", table_name="deployment_runs")
    op.drop_index("ix_deployment_runs_status", table_name="deployment_runs")
    op.drop_index("ix_deployment_runs_application_id", table_name="deployment_runs")
    op.drop_table("deployment_runs")
