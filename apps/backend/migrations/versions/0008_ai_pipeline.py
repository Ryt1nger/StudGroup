"""Durable AI processing, conservative billing reservations and owner incidents."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("raw_messages", sa.Column("reply_to_message_id", sa.BigInteger()))
    op.create_table(
        "ai_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("spent_usd", sa.Numeric(12, 8), nullable=False),
    )
    op.execute("INSERT INTO ai_control (id, spent_usd) VALUES (1, 0)")
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "raw_message_id", sa.Uuid(), sa.ForeignKey("raw_messages.id", ondelete="SET NULL")
        ),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("raw_message_id", "source_revision"),
    )
    op.create_index("ix_ai_jobs_group_id", "ai_jobs", ["group_id"])
    op.create_table(
        "ai_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("charged_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_attempts_group_id", "ai_attempts", ["group_id"])
    op.create_table(
        "owner_incidents",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("notification_pending", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("owner_incidents")
    op.drop_table("ai_attempts")
    op.drop_table("ai_jobs")
    op.drop_table("ai_control")
    op.drop_column("raw_messages", "reply_to_message_id")
