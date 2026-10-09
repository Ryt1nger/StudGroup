"""Source-scoped online joining links; manual schedule URLs remain authoritative."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lesson_online_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("context_id", sa.Uuid(), sa.ForeignKey("raw_messages.id", ondelete="SET NULL")),
        sa.Column("context_revision", sa.Integer()),
        sa.Column(
            "pattern_id", sa.Uuid(), sa.ForeignKey("schedule_patterns.id", ondelete="CASCADE")
        ),
        sa.Column("occurrence_date", sa.Date()),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("group_id", "source_id", "pattern_id"):
        op.create_index(f"ix_lesson_online_links_{column}", "lesson_online_links", [column])


def downgrade():
    op.drop_table("lesson_online_links")
