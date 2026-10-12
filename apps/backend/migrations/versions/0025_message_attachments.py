"""Persist Telegram attachment metadata without downloading file contents."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("stable_file_id", sa.String(255), nullable=False),
        sa.Column("telegram_file_id", sa.String(512), nullable=False),
        sa.Column("file_name", sa.String(512)),
        sa.Column("mime_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("media_group_id", sa.String(255)),
        sa.Column("processing_state", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "raw_message_id",
            "source_revision",
            "kind",
            "stable_file_id",
            name="uq_message_attachment_source_revision",
        ),
    )
    op.create_index("ix_message_attachments_raw_message_id", "message_attachments", ["raw_message_id"])
    op.create_index("ix_message_attachments_group_id", "message_attachments", ["group_id"])
    op.create_index("ix_message_attachments_delete_at", "message_attachments", ["delete_at"])


def downgrade():
    op.drop_index("ix_message_attachments_delete_at", table_name="message_attachments")
    op.drop_index("ix_message_attachments_group_id", table_name="message_attachments")
    op.drop_index("ix_message_attachments_raw_message_id", table_name="message_attachments")
    op.drop_table("message_attachments")
