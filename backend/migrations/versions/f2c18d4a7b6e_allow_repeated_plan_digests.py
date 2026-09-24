"""allow immutable plans with identical rendered content"""

from alembic import op

revision = "f2c18d4a7b6e"
down_revision = "d4e91b03c5a1"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "infrastructure_plans_bundle_digest_key",
        "infrastructure_plans",
        type_="unique",
    )
    op.create_index(
        "ix_infrastructure_plans_bundle_digest", "infrastructure_plans", ["bundle_digest"]
    )


def downgrade():
    op.drop_index("ix_infrastructure_plans_bundle_digest", table_name="infrastructure_plans")
    op.create_unique_constraint(
        "infrastructure_plans_bundle_digest_key",
        "infrastructure_plans",
        ["bundle_digest"],
    )
