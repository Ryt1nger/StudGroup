"""Add per-account next retry time and reason with compatible backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ranepa_crawl_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ranepa_crawl_jobs",
        sa.Column("retry_reason", sa.String(48), nullable=True),
    )
    op.create_index(
        "ix_ranepa_crawl_jobs_retry",
        "ranepa_crawl_jobs",
        ["state", "next_attempt_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE ranepa_crawl_jobs SET next_attempt_at = finished_at + INTERVAL '6 hours' "
            "WHERE finished_at IS NOT NULL AND state IN ('succeeded', 'failed', 'covered')"
        )
    else:
        op.execute(
            "UPDATE ranepa_crawl_jobs SET next_attempt_at = datetime(finished_at, '+6 hours') "
            "WHERE finished_at IS NOT NULL AND state IN ('succeeded', 'failed', 'covered')"
        )
    op.execute(
        "UPDATE ranepa_crawl_jobs SET next_attempt_at = NULL, "
        "retry_reason = 'credentials_required' WHERE failure_code = 'login_failed'"
    )


def downgrade():
    op.drop_index("ix_ranepa_crawl_jobs_retry", table_name="ranepa_crawl_jobs")
    op.drop_column("ranepa_crawl_jobs", "retry_reason")
    op.drop_column("ranepa_crawl_jobs", "next_attempt_at")
