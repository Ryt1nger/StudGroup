"""Persist privacy-safe crawl outcomes for reliable owner notifications.

Revision ID: 0066
Revises: 0065
"""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("failure_detail", "diagnostic_code", "last_step"):
        op.add_column("ranepa_crawl_observations", sa.Column(name, sa.String(48), nullable=True))
    for name in (
        "saved_weeks",
        "saved_lessons",
        "saved_sdo_links",
        "discovered_sdo_candidates",
        "saved_sdo_courses",
    ):
        op.add_column("ranepa_crawl_observations", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column(
        "ranepa_crawl_observations",
        sa.Column("notification_state", sa.String(16), nullable=True),
    )
    op.create_index(
        "ix_ranepa_crawl_observations_notification_state",
        "ranepa_crawl_observations",
        ["notification_state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ranepa_crawl_observations_notification_state",
        table_name="ranepa_crawl_observations",
    )
    for name in (
        "notification_state",
        "saved_sdo_courses",
        "discovered_sdo_candidates",
        "saved_sdo_links",
        "saved_lessons",
        "saved_weeks",
        "last_step",
        "diagnostic_code",
        "failure_detail",
    ):
        op.drop_column("ranepa_crawl_observations", name)
