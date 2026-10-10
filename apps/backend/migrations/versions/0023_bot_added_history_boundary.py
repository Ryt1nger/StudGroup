"""Restrict structure-first bootstrap to history observed after bot installation."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("groups", sa.Column("bot_added_at", sa.DateTime(timezone=True)))
    bind = op.get_bind()
    # Older installations did not persist Telegram's join service event. The
    # first live message is the earliest trustworthy boundary we can recover;
    # imported archive rows must never move it backwards.
    bind.execute(
        sa.text("""
        UPDATE groups AS g
        SET bot_added_at = live.first_message_at
        FROM (
            SELECT group_id, MIN(message_date) AS first_message_at
            FROM raw_messages
            WHERE live_received_at IS NOT NULL AND imported = false
            GROUP BY group_id
        ) AS live
        WHERE g.id = live.group_id
        """)
    )
    # Retire the incorrectly broad package without deleting its audit trail.
    bind.execute(
        sa.text("""
        UPDATE ai_runs AS run
        SET finished_at = CURRENT_TIMESTAMP, outcome = 'superseded'
        FROM group_ai_profiles AS profile
        WHERE run.group_id = profile.group_id
          AND run.analysis_generation = profile.generation
          AND profile.state = 'backfill'
          AND run.finished_at IS NULL
        """)
    )
    bind.execute(
        sa.text("""
        UPDATE ai_jobs AS job
        SET state = 'superseded', lease_until = NULL
        FROM raw_messages AS raw, groups AS g, group_ai_profiles AS profile
        WHERE job.raw_message_id = raw.id
          AND raw.group_id = g.id
          AND profile.group_id = raw.group_id
          AND profile.state = 'backfill'
          AND job.analysis_generation = profile.generation
          AND (g.bot_added_at IS NULL OR raw.message_date < g.bot_added_at)
          AND job.state IN ('queued', 'retry', 'running')
        """)
    )
    bind.execute(
        sa.text("""
        UPDATE raw_messages AS raw
        SET processing_state = 'completed'
        FROM groups AS g, group_ai_profiles AS profile
        WHERE raw.group_id = g.id
          AND profile.group_id = raw.group_id
          AND profile.state = 'backfill'
          AND raw.analysis_generation = profile.generation
          AND (g.bot_added_at IS NULL OR raw.message_date < g.bot_added_at)
          AND raw.processing_state IN ('pending', 'processing', 'failed', 'needs_context')
        """)
    )
    bind.execute(
        sa.text("""
        UPDATE group_ai_profiles AS profile
        SET source_count = scope.message_count
        FROM (
            SELECT g.id AS group_id, COUNT(raw.id) AS message_count
            FROM groups AS g
            LEFT JOIN raw_messages AS raw
              ON raw.group_id = g.id
             AND g.bot_added_at IS NOT NULL
             AND raw.message_date >= g.bot_added_at
            GROUP BY g.id
        ) AS scope
        WHERE profile.group_id = scope.group_id
        """)
    )


def downgrade():
    op.drop_column("groups", "bot_added_at")
