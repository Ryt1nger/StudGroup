"""Store explainable intelligent-deadline state and timetable audit metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "canonical_academic_items",
        sa.Column("deadline_reason", sa.String(64), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "canonical_academic_items",
        sa.Column("timezone_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "canonical_academic_items",
        sa.Column("locked_fields", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "canonical_deadline_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_academic_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_date_only", sa.Boolean(), nullable=False),
        sa.Column("deadline_basis", sa.String(32), nullable=False),
        sa.Column("deadline_reason", sa.String(64), nullable=False),
        sa.Column("timezone_confirmed", sa.Boolean(), nullable=False),
        sa.Column("source_telegram_message_id", sa.BigInteger()),
        sa.Column("source_reply_to_message_id", sa.BigInteger()),
        sa.Column("source_revision", sa.Integer()),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "item_id", "item_revision", name="uq_canonical_deadline_history_revision"
        ),
    )
    op.create_index(
        "ix_canonical_deadline_history_item_id", "canonical_deadline_history", ["item_id"]
    )
    op.create_table(
        "group_no_lesson_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "day", name="uq_group_no_lesson_day"),
    )
    op.create_index("ix_group_no_lesson_days_group_id", "group_no_lesson_days", ["group_id"])
    op.create_table(
        "schedule_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_role", sa.String(16)),
        sa.Column("input_method", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('group', 'personal')", name="schedule_change_scope"),
    )
    op.create_index("ix_schedule_changes_group_id", "schedule_changes", ["group_id"])


def downgrade():
    op.drop_index("ix_schedule_changes_group_id", table_name="schedule_changes")
    op.drop_table("schedule_changes")
    op.drop_index("ix_group_no_lesson_days_group_id", table_name="group_no_lesson_days")
    op.drop_table("group_no_lesson_days")
    op.drop_index("ix_canonical_deadline_history_item_id", table_name="canonical_deadline_history")
    op.drop_table("canonical_deadline_history")
    op.drop_column("canonical_academic_items", "locked_fields")
    op.drop_column("canonical_academic_items", "timezone_confirmed")
    op.drop_column("canonical_academic_items", "deadline_reason")
