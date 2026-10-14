"""Keep the latest successful preflight admission result with its run."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "2f73b94e8c16"
down_revision = "1c52d8a4f6b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deployment_runs", sa.Column("preflight_validation_json", JSONB()))


def downgrade():
    op.drop_column("deployment_runs", "preflight_validation_json")
