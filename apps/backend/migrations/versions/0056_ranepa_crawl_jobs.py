"""Durable group-scoped weekly crawl jobs with no credentials in payloads."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranepa_crawl_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(48)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "user_id", "week_start"),
    )
    op.create_index("ix_ranepa_crawl_jobs_group_id", "ranepa_crawl_jobs", ["group_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ranepa_crawl_jobs ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_ranepa_crawl_jobs_group_id", table_name="ranepa_crawl_jobs")
    op.drop_table("ranepa_crawl_jobs")
