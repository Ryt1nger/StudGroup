"""Keep graded deadlines separate from homework and personal completion."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "academic_deadlines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "raw_message_id", sa.Uuid(), sa.ForeignKey("raw_messages.id", ondelete="SET NULL")
        ),
        sa.Column("import_key", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("date_only", sa.Boolean(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("date_hint", sa.String(1000)),
        sa.Column("needs_clarification", sa.Boolean(), nullable=False),
        sa.Column("source_message_ids", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "import_key"),
    )
    op.create_index("ix_academic_deadlines_group_id", "academic_deadlines", ["group_id"])


def downgrade():
    op.drop_table("academic_deadlines")
