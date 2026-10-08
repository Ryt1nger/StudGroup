"""Independent command panels and tracked private dialogue messages."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_panels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger()),
        sa.UniqueConstraint("chat_id", "mode"),
    )
    op.create_table(
        "bot_panel_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "panel_id",
            sa.Uuid(),
            sa.ForeignKey("bot_panels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.UniqueConstraint("panel_id", "message_id"),
    )
    op.create_table(
        "bot_dialog_routes",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("mode", sa.String(10), nullable=False),
    )
    op.add_column(
        "bot_outbox",
        sa.Column("panel_id", sa.Uuid(), sa.ForeignKey("bot_panels.id", ondelete="CASCADE")),
    )
    op.add_column("bot_outbox", sa.Column("panel_generation", sa.Integer()))
    op.add_column("bot_outbox", sa.Column("panel_revision", sa.Integer()))


def downgrade():
    for column in ["panel_revision", "panel_generation", "panel_id"]:
        op.drop_column("bot_outbox", column)
    for table in ["bot_dialog_routes", "bot_panel_messages", "bot_panels"]:
        op.drop_table(table)
