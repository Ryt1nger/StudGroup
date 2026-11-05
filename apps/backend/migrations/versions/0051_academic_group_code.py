"""Official university group identifier, independent of Telegram chat title."""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups",
        sa.Column("academic_group_code", sa.String(80), nullable=True),
    )
    op.add_column(
        "groups",
        sa.Column(
            "academic_group_code_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("groups", "academic_group_code_required")
    op.drop_column("groups", "academic_group_code")
