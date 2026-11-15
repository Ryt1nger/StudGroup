"""Persist one-time SDO/LC announcement receipt per user, not per device."""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("sdo_launch_prompt_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("user_profiles", "sdo_launch_prompt_seen_at")
