"""Store sanitized Phase 5 Kubernetes admission results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0e7d2b9c4a31"
down_revision = "f2c18d4a7b6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "infrastructure_plan_validations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("infrastructure_plans.id"), nullable=False),
        sa.Column("bundle_digest", sa.String(64), nullable=False),
        sa.Column("environment_profile_hash", sa.String(64), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_infrastructure_plan_validations_plan_id", "infrastructure_plan_validations", ["plan_id"])


def downgrade():
    op.drop_index("ix_infrastructure_plan_validations_plan_id", table_name="infrastructure_plan_validations")
    op.drop_table("infrastructure_plan_validations")
