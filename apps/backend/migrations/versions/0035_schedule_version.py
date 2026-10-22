"""Count group timetable changes so every schedule_changes row names the version it produced."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups",
        sa.Column("schedule_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("groups", "schedule_version")
