"""Persistent topic map and generation-scoped full-history bootstrap."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("raw_messages", sa.Column("message_thread_id", sa.BigInteger()))
    op.add_column(
        "raw_messages",
        sa.Column("analysis_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "ai_jobs",
        sa.Column("analysis_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "ai_runs",
        sa.Column("analysis_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint("ai_runs_group_id_slot_key", "ai_runs", type_="unique")
    op.create_unique_constraint(
        "uq_ai_runs_group_slot_generation",
        "ai_runs",
        ["group_id", "slot", "analysis_generation"],
    )
    op.drop_constraint("ai_jobs_raw_message_id_source_revision_key", "ai_jobs", type_="unique")
    op.create_unique_constraint(
        "uq_ai_jobs_source_generation",
        "ai_jobs",
        ["raw_message_id", "source_revision", "analysis_generation"],
    )
    op.create_table(
        "group_topics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("telegram_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "telegram_thread_id"),
    )
    op.create_index("ix_group_topics_group_id", "group_topics", ["group_id"])
    op.create_table(
        "group_ai_profiles",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("backfill_requested", sa.Boolean(), nullable=False),
        sa.Column("structure_json", sa.Text()),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mapped_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        UPDATE ai_runs SET finished_at = CURRENT_TIMESTAMP, outcome = 'superseded'
        WHERE finished_at IS NULL
        """)
    )
    # A schema-valid empty extraction is a normal high-recall-screen correction,
    # not a provider outage. Release only that legacy circuit reason; real failure
    # circuits and their durable diagnostics remain untouched.
    bind.execute(
        sa.text("""
        UPDATE ai_control
        SET provider_failure_streak = 0,
            provider_circuit_open_until = NULL,
            provider_circuit_reason = NULL
        WHERE provider_circuit_reason = 'zero_yield'
        """)
    )
    bind.execute(
        sa.text("""
        INSERT INTO group_ai_profiles
            (group_id, generation, state, backfill_requested, source_count, requested_at)
        SELECT id, 2, 'mapping', true, 0, CURRENT_TIMESTAMP
        FROM groups WHERE pilot_authorized = true AND status = 'active'
        """)
    )
    bind.execute(
        sa.text("""
        UPDATE raw_messages SET analysis_generation = 2, processing_state = 'pending'
        WHERE group_id IN (
            SELECT group_id FROM group_ai_profiles WHERE generation = 2
        )
        """)


def downgrade():
    op.drop_table("group_ai_profiles")
    op.drop_table("group_topics")
    op.drop_constraint("uq_ai_jobs_source_generation", "ai_jobs", type_="unique")
    op.create_unique_constraint(
        "ai_jobs_raw_message_id_source_revision_key",
        "ai_jobs",
        ["raw_message_id", "source_revision"],
    )
    op.drop_column("ai_jobs", "analysis_generation")
    op.drop_constraint("uq_ai_runs_group_slot_generation", "ai_runs", type_="unique")
    op.create_unique_constraint(
        "ai_runs_group_id_slot_key", "ai_runs", ["group_id", "slot"]
    )
    op.drop_column("ai_runs", "analysis_generation")
    op.drop_column("raw_messages", "analysis_generation")
    op.drop_column("raw_messages", "message_thread_id")
