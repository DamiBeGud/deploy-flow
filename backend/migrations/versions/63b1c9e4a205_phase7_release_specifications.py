"""Store final, admission-validated release bytes without deploying them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "63b1c9e4a205"
down_revision = "5b83cb7e12a9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "release_specifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("infrastructure_plans.id"), nullable=False),
        sa.Column("verified_build_id", sa.Uuid(), sa.ForeignKey("verified_builds.id"), nullable=False),
        sa.Column("approval_id", sa.Uuid(), sa.ForeignKey("plan_approvals.id"), nullable=False),
        sa.Column("environment_profile_id", sa.String(128), nullable=False),
        sa.Column("environment_profile_hash", sa.String(64), nullable=False),
        sa.Column("source_bundle_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("merged_sha", sa.String(40), nullable=False),
        sa.Column("image_reference", sa.String(330), nullable=False),
        sa.Column("validation_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("plan_id", "verified_build_id", name="uq_release_plan_build"),
    )
    op.create_index("ix_release_specifications_application_id", "release_specifications", ["application_id"])
    op.create_index("ix_release_specifications_plan_id", "release_specifications", ["plan_id"])
    op.create_index("ix_release_specifications_verified_build_id", "release_specifications", ["verified_build_id"])
    op.create_table(
        "release_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("release_specifications.id"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("release_id", "path", name="uq_release_file_path"),
    )
    op.create_index("ix_release_files_release_id", "release_files", ["release_id"])


def downgrade():
    op.drop_index("ix_release_files_release_id", table_name="release_files")
    op.drop_table("release_files")
    op.drop_index("ix_release_specifications_verified_build_id", table_name="release_specifications")
    op.drop_index("ix_release_specifications_plan_id", table_name="release_specifications")
    op.drop_index("ix_release_specifications_application_id", table_name="release_specifications")
    op.drop_table("release_specifications")
