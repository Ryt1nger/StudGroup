"""Persistent users, memberships and opaque sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups", sa.Column("status", sa.String(32), nullable=False, server_default="active")
    )
    op.add_column(
        "groups",
        sa.Column("pilot_authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(64)),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("user_id", "group_id"),
        sa.CheckConstraint("status IN ('active', 'suspended', 'left')", name="membership_status"),
        sa.CheckConstraint("role IN ('student', 'headman', 'deputy')", name="membership_role"),
    )
    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("membership_id", sa.Uuid(), sa.ForeignKey("memberships.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("web_sessions")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_column("groups", "pilot_authorized")
    op.drop_column("groups", "status")
