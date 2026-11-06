"""Last successful personal timetable per student and active group."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranepa_personal_snapshots",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("week_start", sa.Date(), primary_key=True),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("data_digest", sa.String(64), nullable=False),
        sa.Column("rows_json", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identity_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_table("ranepa_personal_snapshots")
