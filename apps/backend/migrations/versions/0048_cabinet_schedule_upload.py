"""Private headman timetable uploads; no changes to existing image/file pipelines."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cabinet_schedule_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("duration", sa.String(16), nullable=False),
        sa.Column("rows_json", sa.Text(), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("image_mime", sa.String(32), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("confirmation_json", sa.Text()),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cabinet_schedule_uploads_group_id", "cabinet_schedule_uploads", ["group_id"]
    )
    op.create_index(
        "ix_cabinet_schedule_uploads_delete_at", "cabinet_schedule_uploads", ["delete_at"]
    )


def downgrade():
    op.drop_table("cabinet_schedule_uploads")
