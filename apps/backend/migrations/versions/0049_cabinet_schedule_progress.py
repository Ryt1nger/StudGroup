"""Durable phase-by-phase timetable image processing; progress survives app closes."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cabinet_schedule_uploads",
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "cabinet_schedule_uploads",
        sa.Column("progress_stage", sa.String(32), nullable=False, server_default="legacy"),
    )
    op.add_column("cabinet_schedule_uploads", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("cabinet_schedule_uploads", sa.Column("first_pass_json", sa.Text()))
    op.add_column(
        "cabinet_schedule_uploads",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # Previous synchronous review rows already completed; keep their status and display.
    op.execute(
        "UPDATE cabinet_schedule_uploads SET progress_percent = 100, progress_stage = 'ready' "
        "WHERE state IN ('pending','confirmed')"
    )
    op.execute(
        "UPDATE cabinet_schedule_uploads SET progress_stage = 'failed' WHERE state = 'failed'"
    )
    # Old synchronous requests could be interrupted during rollout; they have
    # no durable checkpoint. Never silently repurchase those paid calls.
    op.execute(
        "UPDATE cabinet_schedule_uploads SET state = 'failed', progress_stage = 'failed' "
        "WHERE state = 'processing'"
    )


def downgrade():
    op.drop_column("cabinet_schedule_uploads", "retry_count")
    op.drop_column("cabinet_schedule_uploads", "first_pass_json")
    op.drop_column("cabinet_schedule_uploads", "claimed_at")
    op.drop_column("cabinet_schedule_uploads", "progress_stage")
    op.drop_column("cabinet_schedule_uploads", "progress_percent")
