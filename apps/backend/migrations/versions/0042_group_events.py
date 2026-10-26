"""Separate social event drafts, source findings and immutable revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title_key", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("manual_lock", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "fingerprint"),
    )
    for column in ("group_id", "title_key", "delete_at"):
        op.create_index(f"ix_group_events_{column}", "group_events", [column])
    op.create_table(
        "group_event_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("group_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_job_id", sa.Uuid(), sa.ForeignKey("image_analysis_jobs.id", ondelete="CASCADE")),
        sa.Column("raw_message_id", sa.Uuid(), sa.ForeignKey("raw_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("incoming_json", sa.Text(), nullable=False),
        sa.Column("apply_state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_event_sources_event_id", "group_event_sources", ["event_id"])
    op.create_table(
        "group_event_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("group_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "revision"),
    )
    op.create_index("ix_group_event_revisions_event_id", "group_event_revisions", ["event_id"])


def downgrade():
    op.drop_table("group_event_revisions")
    op.drop_table("group_event_sources")
    op.drop_table("group_events")
