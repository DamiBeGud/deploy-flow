"""Opt-in deployment execution and resource journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "76e4c12a8b03"
down_revision = "2f73b94e8c16"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deployment_runs", sa.Column("rollout_json", JSONB()))
    op.add_column("deployment_runs", sa.Column("health_json", JSONB()))
    op.add_column("deployment_runs", sa.Column("previous_release_id", sa.Uuid(), sa.ForeignKey("release_specifications.id")))
    op.drop_index("uq_active_deployment_application_environment", table_name="deployment_runs")
    op.create_index(
        "uq_active_deployment_application_environment", "deployment_runs",
        ["application_id", "environment_profile_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling')"),
    )
    op.create_table(
        "deployment_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("environment_profile_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("application_id", "environment_profile_id", name="uq_deployment_policy_target"),
    )
    op.create_index("ix_deployment_policies_application_id", "deployment_policies", ["application_id"])
    op.create_table(
        "execution_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deployment_run_id", sa.Uuid(), sa.ForeignKey("deployment_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("deployment_run_id", name="uq_execution_job_run"),
    )
    op.create_index("ix_execution_jobs_deployment_run_id", "execution_jobs", ["deployment_run_id"])
    op.create_index("ix_execution_jobs_status", "execution_jobs", ["status"])
    op.create_table(
        "deployment_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deployment_run_id", sa.Uuid(), sa.ForeignKey("deployment_runs.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("uid", sa.String(128)),
        sa.Column("generation", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("deployment_run_id", "kind", "name", name="uq_deployment_resource"),
    )
    op.create_index("ix_deployment_resources_deployment_run_id", "deployment_resources", ["deployment_run_id"])


def downgrade():
    op.drop_index("ix_deployment_resources_deployment_run_id", table_name="deployment_resources")
    op.drop_table("deployment_resources")
    op.drop_index("ix_execution_jobs_status", table_name="execution_jobs")
    op.drop_index("ix_execution_jobs_deployment_run_id", table_name="execution_jobs")
    op.drop_table("execution_jobs")
    op.drop_index("ix_deployment_policies_application_id", table_name="deployment_policies")
    op.drop_table("deployment_policies")
    op.drop_index("uq_active_deployment_application_environment", table_name="deployment_runs")
    op.create_index(
        "uq_active_deployment_application_environment", "deployment_runs",
        ["application_id", "environment_profile_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor')"),
    )
    op.drop_column("deployment_runs", "previous_release_id")
    op.drop_column("deployment_runs", "health_json")
    op.drop_column("deployment_runs", "rollout_json")
