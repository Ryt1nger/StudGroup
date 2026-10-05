"""Durable Telegram text inbox and idempotent delivery markers."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "received_updates",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "raw_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("processing_state", sa.String(16), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "telegram_message_id"),
    )


def downgrade():
    op.drop_table("raw_messages")
    op.drop_table("received_updates")
