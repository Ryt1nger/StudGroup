"""Private /start walkthrough state (step and button nonce per Telegram user)."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "start_sessions",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("nonce", sa.String(24), nullable=False),
        sa.Column("step", sa.String(24), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("start_sessions")
