"""Unpublished image timetable suggestions, confirmed only through existing staff writes."""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "image_schedule_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_job_id", sa.Uuid(), sa.ForeignKey("image_analysis_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("rows_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("group_id", "delete_at"):
        op.create_index(f"ix_image_schedule_suggestions_{column}", "image_schedule_suggestions", [column])


def downgrade():
    op.drop_table("image_schedule_suggestions")
