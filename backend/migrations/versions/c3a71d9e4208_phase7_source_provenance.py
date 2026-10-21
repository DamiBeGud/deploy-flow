"""Separate approved infrastructure from application build revisions.

Revision ID: c3a71d9e4208
Revises: 91d2e0a7b534
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3a71d9e4208"
down_revision = "91d2e0a7b534"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deployment_policies", sa.Column("allowed_source_sha", sa.String(40), nullable=True))
    op.add_column("infrastructure_pull_requests", sa.Column("source_head_sha", sa.String(40), nullable=True))
    op.add_column("infrastructure_pull_requests", sa.Column("source_status", sa.String(32), nullable=True))
    op.add_column("infrastructure_pull_requests", sa.Column("source_error_code", sa.String(64), nullable=True))
    op.add_column("verified_builds", sa.Column("source_sha", sa.String(40), nullable=True))
    op.add_column("verified_builds", sa.Column("artifact_id", sa.BigInteger(), nullable=True))
    op.add_column("verified_builds", sa.Column("compatibility_json", postgresql.JSONB(), nullable=True))
    op.add_column("release_specifications", sa.Column("source_sha", sa.String(40), nullable=True))
    op.execute("UPDATE verified_builds SET source_sha = merged_sha")
    op.execute("UPDATE release_specifications SET source_sha = merged_sha")


def downgrade():
    # Historical source provenance must not be silently discarded.
    raise RuntimeError("Restore a reviewed backup; source provenance cannot be discarded safely")
