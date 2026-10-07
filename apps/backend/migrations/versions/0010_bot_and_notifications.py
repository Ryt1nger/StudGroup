"""Private bot administration and real personal notification inbox."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_admin_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_key", sa.String(160), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notifications_group_id", "notifications", ["group_id"])
    op.create_table(
        "notification_reads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("notification_id", "user_id"),
    )
    op.create_table(
        "bot_admin_sessions",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("nonce", sa.String(24), nullable=False),
        sa.Column("step", sa.String(24), nullable=False),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="SET NULL")),
        sa.Column("candidate_id", sa.BigInteger()),
        sa.Column("request_id", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "bot_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("bot_admin_audit")
    for table in ["bot_outbox", "bot_admin_sessions", "notification_reads", "notifications"]:
        op.drop_table(table)
