"""Preserve imported evidence without fabricating a Telegram deep link."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "raw_messages",
        sa.Column("imported", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("raw_messages", "imported")
