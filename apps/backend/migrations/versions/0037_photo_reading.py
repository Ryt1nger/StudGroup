"""Store what the vision model read on a photo attachment (text and a short summary)."""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("message_attachments", sa.Column("photo_text", sa.Text()))
    op.add_column("message_attachments", sa.Column("photo_summary", sa.String(500)))
    op.add_column("message_attachments", sa.Column("read_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("message_attachments", "read_at")
    op.drop_column("message_attachments", "photo_summary")
    op.drop_column("message_attachments", "photo_text")
