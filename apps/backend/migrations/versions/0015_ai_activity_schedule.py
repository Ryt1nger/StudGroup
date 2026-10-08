"""Persist live activity signals and per-message half-hour attempt slots."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("raw_messages", sa.Column("live_received_at", sa.DateTime(timezone=True)))
    op.add_column("ai_jobs", sa.Column("schedule_slot", sa.DateTime(timezone=True)))
    op.create_table(
        "group_ai_activity",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("last_batch_slot", sa.DateTime(timezone=True)),
    )
    # Only live messages are adopted. Imported history must not wake the new scheduler.
    op.execute("UPDATE raw_messages SET live_received_at = version_date WHERE imported = false")
    op.execute(
        "INSERT INTO group_ai_activity (group_id, last_signal_at, signal_count) SELECT group_id, max(live_received_at), count(*) FROM raw_messages WHERE imported = false GROUP BY group_id"
    )


def downgrade():
    op.drop_table("group_ai_activity")
    op.drop_column("ai_jobs", "schedule_slot")
    op.drop_column("raw_messages", "live_received_at")
