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
    op.create_table(
        "ranepa_crawl_observations",
        sa.Column(
            "job_id", sa.Uuid(),
            sa.ForeignKey("ranepa_crawl_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempt", sa.Integer(), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_code", sa.String(48), nullable=False),
        sa.Column("captcha_seen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("captcha_location", sa.String(16)),
        sa.Column("preceding_step", sa.String(48)),
        sa.Column("session_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("events_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_ranepa_crawl_observations_observed_at",
        "ranepa_crawl_observations",
        ["observed_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ranepa_crawl_guard ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ranepa_crawl_observations ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index(
        "ix_ranepa_crawl_observations_observed_at",
        table_name="ranepa_crawl_observations",
    )
    op.drop_table("ranepa_crawl_observations")
    op.drop_table("ranepa_crawl_guard")
