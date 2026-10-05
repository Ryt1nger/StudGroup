"""Homework cards and personal completion state."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "homework",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_name", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_date_only", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("urgency", sa.String(16), nullable=False),
        sa.Column("verification_state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("significant_updated_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_homework_group_id", "homework", ["group_id"])
    op.create_table(
        "personal_completions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "homework_id",
            sa.Uuid(),
            sa.ForeignKey("homework.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_revision", sa.Integer()),
        sa.UniqueConstraint("homework_id", "user_id"),
    )


def downgrade():
    op.drop_table("personal_completions")
    op.drop_table("homework")
