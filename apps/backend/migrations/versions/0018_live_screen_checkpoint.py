"""Durable live screening result; no historical replay or paid calls."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_jobs", sa.Column("screen_checkpoint", sa.Text(), nullable=True))
    # Repair only recent live text discarded without ANY model job. Historical
    # imports, successful/failed paid jobs and manual decisions are not replayed.
    changed = op.get_bind().execute(
        sa.text("""
        UPDATE raw_messages SET processing_state = 'pending'
        WHERE imported = false AND processing_state = 'completed'
          AND live_received_at IS NOT NULL AND delete_at > CURRENT_TIMESTAMP
          AND message_date >= CURRENT_TIMESTAMP - INTERVAL '7 days'
          AND EXISTS (SELECT 1 FROM groups g WHERE g.id = raw_messages.group_id
                      AND g.status = 'active' AND g.pilot_authorized = true)
          AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.raw_message_id = raw_messages.id)
    """)
    )
    print(f"LIVE_SCREEN_BACKLOG_QUEUED {changed.rowcount}", flush=True)


def downgrade():
    op.drop_column("ai_jobs", "screen_checkpoint")
