"""Track explicit first-deployment cleanup as a fenced execution purpose."""

import sqlalchemy as sa
from alembic import op

revision = "91d2e0a7b534"
down_revision = "8c27b4f1a902"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("execution_jobs", sa.Column(
        "purpose", sa.String(32), nullable=False, server_default="deploy",
    ))
    op.drop_index("uq_active_deployment_application_environment", table_name="deployment_runs")
    op.create_index(
        "uq_active_deployment_application_environment", "deployment_runs",
        ["application_id", "environment_profile_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling', 'cleanup_pending', 'cleaning')"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM execution_jobs WHERE purpose = 'cleanup')"
    )):
        raise RuntimeError("Cleanup history must be preserved; this migration cannot be reversed")
    op.drop_index("uq_active_deployment_application_environment", table_name="deployment_runs")
    op.create_index(
        "uq_active_deployment_application_environment", "deployment_runs",
        ["application_id", "environment_profile_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling')"),
    )
    op.drop_column("execution_jobs", "purpose")
