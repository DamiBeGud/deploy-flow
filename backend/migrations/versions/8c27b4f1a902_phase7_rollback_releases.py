"""Allow a new immutable release for each authorized manual rollback."""

import sqlalchemy as sa
from alembic import op

revision = "8c27b4f1a902"
down_revision = "76e4c12a8b03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("release_specifications", sa.Column(
        "kind", sa.String(32), nullable=False, server_default="standard",
    ))
    op.add_column("release_specifications", sa.Column(
        "rollback_target_release_id", sa.Uuid(), sa.ForeignKey("release_specifications.id"),
    ))
    op.add_column("release_specifications", sa.Column(
        "rollback_from_release_id", sa.Uuid(), sa.ForeignKey("release_specifications.id"),
    ))
    op.drop_constraint("uq_release_plan_build", "release_specifications", type_="unique")
    op.create_index(
        "uq_standard_release_plan_build", "release_specifications",
        ["plan_id", "verified_build_id"], unique=True,
        postgresql_where=sa.text("kind = 'standard'"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM release_specifications WHERE kind = 'rollback')"
    )):
        raise RuntimeError("Rollback releases must be preserved; this migration cannot be reversed")
    op.drop_index("uq_standard_release_plan_build", table_name="release_specifications")
    op.create_unique_constraint(
        "uq_release_plan_build", "release_specifications", ["plan_id", "verified_build_id"],
    )
    op.drop_column("release_specifications", "rollback_from_release_id")
    op.drop_column("release_specifications", "rollback_target_release_id")
    op.drop_column("release_specifications", "kind")
