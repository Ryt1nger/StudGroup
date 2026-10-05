"""Group calendar and recurring lesson patterns."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("first_week_anchor", sa.Date(), nullable=True),
    )
    op.create_table(
        "schedule_patterns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Time(), nullable=False),
        sa.Column("ends", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("week", sa.String(8), nullable=False),
        sa.Column("teacher", sa.String(255)),
        sa.Column("location", sa.String(512)),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="schedule_weekday"),
        sa.CheckConstraint("ends > starts", name="schedule_time"),
        sa.CheckConstraint("valid_until >= valid_from", name="schedule_validity"),
        sa.CheckConstraint("week IN ('all', 'first', 'second')", name="schedule_week"),
    )
    op.create_index("ix_schedule_patterns_group_id", "schedule_patterns", ["group_id"])


def downgrade():
    op.drop_table("schedule_patterns")
    op.drop_table("groups")
