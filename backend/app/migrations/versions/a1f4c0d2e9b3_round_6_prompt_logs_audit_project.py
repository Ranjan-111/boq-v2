"""round 6: prompt logs + audit project scoping

Round 6 AI + review workspace (T060, T065, T075):
  * prompt_logs - every AI provider call is recorded verbatim (provider,
    model, prompt, JSON-schema response, created_at) BEFORE any suggestion is
    derived from it. The audit trail is a precondition for trusting an AI
    layer that only ever writes advisory rows. ai_suggestions.prompt_log_id
    already exists (baseline) and now gains its target table.
  * audit_log.project_id - nullable, indexed. Round 4/5 wrote project-scoped
    actions (scale confirm, resolve, approve, export) into a table queryable
    only by actor/subject; GET /projects/{pid}/audit (T075) needs the direct
    index. Nullable by design: system/worker actions may be project-less;
    every future project-scoped write sets it.

Down-rev: 93865264fc81 (round 4).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1f4c0d2e9b3"
down_revision = "93865264fc81"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Structured (JSON-schema-validated) response, stored verbatim.
        sa.Column(
            "response", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_logs")),
    )
    op.create_index(
        op.f("ix_prompt_logs_created_at"), "prompt_logs", ["created_at"],
        unique=False)

    op.add_column(
        "audit_log",
        sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_audit_log_project_id"), "audit_log", ["project_id"],
        unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_project_id"), table_name="audit_log")
    op.drop_column("audit_log", "project_id")
    op.drop_index(op.f("ix_prompt_logs_created_at"), table_name="prompt_logs")
    op.drop_table("prompt_logs")
