"""Independent cancellation timestamp; no homework deletion."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("homework", sa.Column("cancelled_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("homework", "cancelled_at")
