"""Subjects taught in subgroups, the members' choices and the subgroup a card belongs to."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subject_splits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "subject_id"),
    )
    op.create_index("ix_subject_splits_group_id", "subject_splits", ["group_id"])
    op.create_table(
        "subgroups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "split_id",
            sa.Uuid(),
            sa.ForeignKey("subject_splits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("signals", sa.Text(), nullable=False, server_default="[]"),
        sa.UniqueConstraint("split_id", "position"),
    )
    op.create_index("ix_subgroups_split_id", "subgroups", ["split_id"])
    op.create_table(
        "member_subgroups",
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "split_id",
            sa.Uuid(),
            sa.ForeignKey("subject_splits.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "subgroup_id",
            sa.Uuid(),
            sa.ForeignKey("subgroups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_member_subgroups_split_id", "member_subgroups", ["split_id"])
    op.create_index("ix_member_subgroups_subgroup_id", "member_subgroups", ["subgroup_id"])
    for table in ("homework", "academic_deadlines"):
        op.add_column(
            table,
            sa.Column(
                "subgroup_id",
                sa.Uuid(),
                sa.ForeignKey("subgroups.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_subgroup_id", table, ["subgroup_id"])


def downgrade():
    for table in ("homework", "academic_deadlines"):
        op.drop_index(f"ix_{table}_subgroup_id", table_name=table)
        op.drop_column(table, "subgroup_id")
    op.drop_table("member_subgroups")
    op.drop_table("subgroups")
    op.drop_table("subject_splits")
