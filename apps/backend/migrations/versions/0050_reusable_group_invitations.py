"""Reusable, revocable invitations for verified Telegram group members."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "group_invitations",
        sa.Column(
            "reusable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("group_invitations", "reusable")
