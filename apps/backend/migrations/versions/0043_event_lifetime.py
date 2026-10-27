"""Editor-approved event lifetime independent of private source retention."""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    # Existing gated drafts stay drafts; do not manufacture approval for old records.
    op.add_column("group_events", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("group_events", sa.Column("approved_by", sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_group_event_approver")))
    op.add_column("group_events", sa.Column("approved_payload_sha256", sa.String(64)))
    op.add_column("group_events", sa.Column("event_end_date", sa.Date()))


def downgrade():
    op.drop_column("group_events", "event_end_date")
    op.drop_column("group_events", "approved_payload_sha256")
    op.drop_column("group_events", "approved_by")
    op.drop_column("group_events", "published_at")
