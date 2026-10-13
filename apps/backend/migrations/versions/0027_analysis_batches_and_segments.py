"""Add shadow micro-batches, cursors and versioned conversation segments."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_cursors",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.Column("last_completed_sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "analysis_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("source_versions", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("group_id", "sequence", name="uq_analysis_batch_group_sequence"),
    )
    op.create_index("ix_analysis_batches_group_id", "analysis_batches", ["group_id"])
    op.create_table(
        "conversation_segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stable_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "group_id", "stable_key", "version", name="uq_conversation_segment_version"
        ),
    )
    op.create_index("ix_conversation_segments_group_id", "conversation_segments", ["group_id"])
    op.create_index(
        "ix_conversation_segments_active",
        "conversation_segments",
        ["group_id", "state", "last_activity_at"],
    )
    op.create_table(
        "segment_source_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "segment_id",
            sa.Uuid(),
            sa.ForeignKey("conversation_segments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("link_reason", sa.String(32), nullable=False),
        sa.Column("strength", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "segment_id",
            "raw_message_id",
            "source_revision",
            name="uq_segment_source_revision",
        ),
    )
    op.create_index("ix_segment_source_links_segment_id", "segment_source_links", ["segment_id"])
    op.create_index(
        "ix_segment_source_links_raw_message_id", "segment_source_links", ["raw_message_id"]
    )


def downgrade():
    op.drop_index("ix_segment_source_links_raw_message_id", table_name="segment_source_links")
    op.drop_index("ix_segment_source_links_segment_id", table_name="segment_source_links")
    op.drop_table("segment_source_links")
    op.drop_index("ix_conversation_segments_active", table_name="conversation_segments")
    op.drop_index("ix_conversation_segments_group_id", table_name="conversation_segments")
    op.drop_table("conversation_segments")
    op.drop_index("ix_analysis_batches_group_id", table_name="analysis_batches")
    op.drop_table("analysis_batches")
    op.drop_table("analysis_cursors")
