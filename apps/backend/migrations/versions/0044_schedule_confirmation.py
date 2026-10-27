"""Explicit editor corrections and subgroup-aware timetable metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("image_schedule_suggestions", sa.Column("confirmation_json", sa.Text()))
    op.add_column("schedule_patterns", sa.Column("subgroup_id", sa.Uuid(),
        sa.ForeignKey("subgroups.id", ondelete="SET NULL", name="fk_schedule_pattern_subgroup")))
    op.add_column("schedule_exceptions", sa.Column("subject", sa.String(255)))
    op.add_column("schedule_exceptions", sa.Column("teacher", sa.String(255)))
    op.add_column("schedule_exceptions", sa.Column("subgroup_id", sa.Uuid(),
        sa.ForeignKey("subgroups.id", ondelete="SET NULL", name="fk_schedule_exception_subgroup")))
    op.add_column("schedule_exceptions", sa.Column("teacher_overridden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("schedule_exceptions", sa.Column("subgroup_overridden", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    for column in ("teacher_overridden", "subgroup_overridden", "subgroup_id", "teacher", "subject"):
        op.drop_column("schedule_exceptions", column)
    op.drop_column("schedule_patterns", "subgroup_id")
    op.drop_column("image_schedule_suggestions", "confirmation_json")
