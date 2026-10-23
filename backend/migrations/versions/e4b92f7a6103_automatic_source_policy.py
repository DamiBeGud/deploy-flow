"""Explicit opt-in for broader Node handler updates; preserve existing authority.

Revision ID: e4b92f7a6103
Revises: c3a71d9e4208
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b92f7a6103"
down_revision = "c3a71d9e4208"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deployment_policies", sa.Column("source_compatibility_policy", sa.String(64), nullable=False,
                  server_default="node-source-compatibility-v1"))


def downgrade():
    raise RuntimeError("Review a compatible fallback and retained policy history; do not silently discard deployment authorization")
