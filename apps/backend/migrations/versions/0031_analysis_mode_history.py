"""Audit controlled analysis-mode switches without changing product data."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_analysis_mode_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_mode", sa.String(16), nullable=False),
        sa.Column("new_mode", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_group_analysis_mode_history_group_id",
        "group_analysis_mode_history",
        ["group_id"],
    )


def downgrade():
    op.drop_index(
        "ix_group_analysis_mode_history_group_id",
        table_name="group_analysis_mode_history",
    )
    op.drop_table("group_analysis_mode_history")
