"""phase3 skills, agent provenance and durable budgets"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7e82c9d4f12"
down_revision = "9f62b89f6b11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "analysis_runs",
        sa.Column("scanner_facts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("analysis_runs", sa.Column("model_provider", sa.String(length=32), nullable=True))
    op.add_column("analysis_runs", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.add_column("analysis_runs", sa.Column("prompt_version", sa.String(length=64), nullable=True))
    for name in (
        "agent_turns",
        "agent_tool_calls",
        "agent_input_tokens",
        "agent_output_tokens",
        "agent_tool_output_bytes",
        "agent_latency_ms",
    ):
        op.add_column(
            "analysis_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column("evidence_items", sa.Column("evidence_key", sa.String(length=128), nullable=True))
    op.create_index(
        "ix_evidence_items_run_key", "evidence_items", ["run_id", "evidence_key"], unique=True
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("references_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "skill_id", "semantic_version", "content_hash", name="uq_skill_version_content"
        ),
    )
    op.create_index(
        op.f("ix_skill_versions_skill_id"), "skill_versions", ["skill_id"], unique=False
    )
    op.create_table(
        "analysis_run_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("skill_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_analysis_run_skill_ordinal"),
    )
    op.create_index(
        op.f("ix_analysis_run_skills_run_id"), "analysis_run_skills", ["run_id"], unique=False
    )
    op.create_table(
        "agent_tool_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("arguments_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "turn", "call_id", name="uq_agent_tool_event_call"),
    )
    op.create_index(
        op.f("ix_agent_tool_events_run_id"), "agent_tool_events", ["run_id"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_agent_tool_events_run_id"), table_name="agent_tool_events")
    op.drop_table("agent_tool_events")
    op.drop_index(op.f("ix_analysis_run_skills_run_id"), table_name="analysis_run_skills")
    op.drop_table("analysis_run_skills")
    op.drop_index(op.f("ix_skill_versions_skill_id"), table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index("ix_evidence_items_run_key", table_name="evidence_items")
    op.drop_column("evidence_items", "evidence_key")
    for name in (
        "agent_latency_ms",
        "agent_tool_output_bytes",
        "agent_output_tokens",
        "agent_input_tokens",
        "agent_tool_calls",
        "agent_turns",
        "prompt_version",
        "model_name",
        "model_provider",
        "scanner_facts_json",
    ):
        op.drop_column("analysis_runs", name)
