"""Add evidence-backed structured group knowledge and append-only history."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_knowledge_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_type", sa.String(32), nullable=False),
        sa.Column("normalized_key", sa.String(255), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("verification_source", sa.String(32)),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "group_id",
            "fact_type",
            "normalized_key",
            "revision",
            name="uq_group_knowledge_fact_revision",
        ),
    )
    op.create_index("ix_group_knowledge_facts_group_id", "group_knowledge_facts", ["group_id"])
    op.create_index(
        "ix_group_knowledge_active",
        "group_knowledge_facts",
        ["group_id", "status", "fact_type"],
    )
    op.create_table(
        "group_knowledge_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "fact_id",
            sa.Uuid(),
            sa.ForeignKey("group_knowledge_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("evidence_role", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "fact_id",
            "raw_message_id",
            "source_revision",
            name="uq_group_knowledge_evidence_revision",
        ),
    )
    op.create_index("ix_group_knowledge_evidence_fact_id", "group_knowledge_evidence", ["fact_id"])
    op.create_index(
        "ix_group_knowledge_evidence_raw_message_id",
        "group_knowledge_evidence",
        ["raw_message_id"],
    )
    op.create_table(
        "group_knowledge_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "fact_id",
            sa.Uuid(),
            sa.ForeignKey("group_knowledge_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fact_id", "fact_revision", name="uq_group_knowledge_history_revision"),
    )
    op.create_index("ix_group_knowledge_history_fact_id", "group_knowledge_history", ["fact_id"])


def downgrade():
    op.drop_index("ix_group_knowledge_history_fact_id", table_name="group_knowledge_history")
    op.drop_table("group_knowledge_history")
    op.drop_index(
        "ix_group_knowledge_evidence_raw_message_id", table_name="group_knowledge_evidence"
    )
    op.drop_index("ix_group_knowledge_evidence_fact_id", table_name="group_knowledge_evidence")
    op.drop_table("group_knowledge_evidence")
    op.drop_index("ix_group_knowledge_active", table_name="group_knowledge_facts")
    op.drop_index("ix_group_knowledge_facts_group_id", table_name="group_knowledge_facts")
    op.drop_table("group_knowledge_facts")
