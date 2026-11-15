"""Cross-run CAPTCHA circuit breaker without changing existing fair crawl jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranepa_crawl_guard",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consecutive_captcha", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recovery_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_job_id", sa.Uuid(), nullable=True),
        sa.Column("paid_day", sa.Date(), nullable=True),
        sa.Column("paid_tasks_reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ranepa_crawl_guard_singleton"),
        sa.CheckConstraint("consecutive_captcha >= 0", name="ck_ranepa_crawl_guard_nonnegative"),
        sa.CheckConstraint("paid_tasks_reserved >= 0", name="ck_ranepa_crawl_guard_paid_nonnegative"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ranepa_crawl_guard ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("ranepa_crawl_guard")
