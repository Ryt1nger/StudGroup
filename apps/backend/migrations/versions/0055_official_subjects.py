"""Group-isolated university subjects from verified timetable snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_official_subjects",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("name_key", sa.String(255), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE group_official_subjects ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("group_official_subjects")
