"""phase4 reviewed intents and immutable infrastructure plans"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4e91b03c5a1"
down_revision = "a7e82c9d4f12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_intent_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "version", name="uq_intent_version"),
    )
    op.create_index(
        "ix_deployment_intent_versions_application_id",
        "deployment_intent_versions",
        ["application_id"],
    )
    op.create_index(
        "ix_deployment_intent_versions_analysis_run_id",
        "deployment_intent_versions",
        ["analysis_run_id"],
    )
    op.create_table(
        "infrastructure_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_version", sa.Integer(), nullable=False),
        sa.Column("intent_version_id", sa.Uuid(), nullable=False),
        sa.Column("environment_profile_id", sa.String(length=128), nullable=False),
        sa.Column("environment_profile_hash", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("bundle_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("validation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("previous_plan_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["intent_version_id"], ["deployment_intent_versions.id"]),
        sa.ForeignKeyConstraint(["previous_plan_id"], ["infrastructure_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_digest"),
    )
    op.create_index(
        "ix_infrastructure_plans_application_id", "infrastructure_plans", ["application_id"]
    )
    op.create_index(
        "ix_infrastructure_plans_analysis_run_id", "infrastructure_plans", ["analysis_run_id"]
    )
    op.create_index("ix_infrastructure_plans_status", "infrastructure_plans", ["status"])
    op.create_table(
        "infrastructure_plan_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["infrastructure_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "path", name="uq_plan_file_path"),
    )
    op.create_index(
        "ix_infrastructure_plan_files_plan_id", "infrastructure_plan_files", ["plan_id"]
    )
    op.create_table(
        "plan_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("bundle_digest", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["infrastructure_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_approvals_plan_id", "plan_approvals", ["plan_id"])


def downgrade():
    op.drop_index("ix_plan_approvals_plan_id", table_name="plan_approvals")
    op.drop_table("plan_approvals")
    op.drop_index("ix_infrastructure_plan_files_plan_id", table_name="infrastructure_plan_files")
    op.drop_table("infrastructure_plan_files")
    op.drop_index("ix_infrastructure_plans_status", table_name="infrastructure_plans")
    op.drop_index("ix_infrastructure_plans_analysis_run_id", table_name="infrastructure_plans")
    op.drop_index("ix_infrastructure_plans_application_id", table_name="infrastructure_plans")
    op.drop_table("infrastructure_plans")
    op.drop_index(
        "ix_deployment_intent_versions_analysis_run_id", table_name="deployment_intent_versions"
    )
    op.drop_index(
        "ix_deployment_intent_versions_application_id", table_name="deployment_intent_versions"
    )
    op.drop_table("deployment_intent_versions")
