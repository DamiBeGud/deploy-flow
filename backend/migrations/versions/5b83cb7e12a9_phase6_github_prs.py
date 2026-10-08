"""Durable GitHub PR jobs, signed webhook deliveries, and verified builds."""

import sqlalchemy as sa
from alembic import op

revision = "5b83cb7e12a9"
down_revision = "0e7d2b9c4a31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "infrastructure_pull_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("infrastructure_plans.id"), nullable=False),
        sa.Column("repository_id", sa.BigInteger()),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("repository", sa.String(128), nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("head_branch", sa.String(255), nullable=False),
        sa.Column("head_sha", sa.String(40)),
        sa.Column("number", sa.Integer()),
        sa.Column("url", sa.String(1024)),
        sa.Column("merged_sha", sa.String(40)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("plan_id", name="uq_infrastructure_pr_plan"),
    )
    op.create_index("ix_infrastructure_pull_requests_plan_id", "infrastructure_pull_requests", ["plan_id"])
    op.create_index("ix_infrastructure_pull_requests_status", "infrastructure_pull_requests", ["status"])
    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(128), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "github_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("pull_request_id", sa.Uuid(), sa.ForeignKey("infrastructure_pull_requests.id")),
        sa.Column("delivery_id", sa.String(128), sa.ForeignKey("github_webhook_deliveries.delivery_id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("next_dispatch_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_github_jobs_pull_request_id", "github_jobs", ["pull_request_id"])
    op.create_index("ix_github_jobs_status", "github_jobs", ["status"])
    op.create_table(
        "verified_builds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pull_request_id", sa.Uuid(), sa.ForeignKey("infrastructure_pull_requests.id"), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("merged_sha", sa.String(40), nullable=False),
        sa.Column("image_repository", sa.String(255), nullable=False),
        sa.Column("image_digest", sa.String(71), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("pull_request_id", "run_id", "run_attempt", name="uq_verified_build_run"),
    )
    op.create_index("ix_verified_builds_pull_request_id", "verified_builds", ["pull_request_id"])


def downgrade():
    op.drop_index("ix_verified_builds_pull_request_id", table_name="verified_builds")
    op.drop_table("verified_builds")
    op.drop_index("ix_github_jobs_status", table_name="github_jobs")
    op.drop_index("ix_github_jobs_pull_request_id", table_name="github_jobs")
    op.drop_table("github_jobs")
    op.drop_table("github_webhook_deliveries")
    op.drop_index("ix_infrastructure_pull_requests_status", table_name="infrastructure_pull_requests")
    op.drop_index("ix_infrastructure_pull_requests_plan_id", table_name="infrastructure_pull_requests")
    op.drop_table("infrastructure_pull_requests")
