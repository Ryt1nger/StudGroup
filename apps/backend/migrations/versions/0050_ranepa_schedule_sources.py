"""Isolated source configuration and read-only timetable snapshots per Telegram group."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_schedule_sources",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("official_group_code", sa.String(120), nullable=False),
        sa.Column("upstream_group_uuid", sa.Uuid(), nullable=True),
        sa.Column("collection_mode", sa.String(24), nullable=False),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "group_schedule_snapshots",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("week_start", sa.Date(), primary_key=True),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("data_digest", sa.String(64), nullable=False),
        sa.Column("rows_json", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("group_schedule_snapshots")
    op.drop_table("group_schedule_sources")
