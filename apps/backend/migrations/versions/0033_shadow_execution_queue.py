"""Make C+ shadow evaluation durable, retryable and budget-reserved."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shadow_evaluation_runs", sa.Column("job_id", sa.Uuid()))
    op.add_column("shadow_evaluation_runs", sa.Column("raw_message_id", sa.Uuid()))
    op.add_column("shadow_evaluation_runs", sa.Column("source_revision", sa.Integer()))
    op.add_column("shadow_evaluation_runs", sa.Column("analysis_generation", sa.Integer()))
    op.add_column("shadow_evaluation_runs", sa.Column("input_json", sa.Text()))
    op.add_column("shadow_evaluation_runs", sa.Column("memory_json", sa.Text()))
    op.add_column("shadow_evaluation_runs", sa.Column("context_result_json", sa.Text()))
    op.add_column("shadow_evaluation_runs", sa.Column("validation_result_json", sa.Text()))
    op.add_column("shadow_evaluation_runs", sa.Column("provider_payload_hashes", sa.Text()))
    op.add_column(
        "shadow_evaluation_runs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("shadow_evaluation_runs", sa.Column("available_at", sa.DateTime(timezone=True)))
    op.add_column("shadow_evaluation_runs", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.add_column("shadow_evaluation_runs", sa.Column("last_error", sa.String(64)))
    op.add_column(
        "shadow_evaluation_runs",
        sa.Column("prompt_cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "shadow_evaluation_runs",
        sa.Column("reserved_usd", sa.Numeric(12, 8), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_shadow_evaluation_runs_job_id",
        "shadow_evaluation_runs",
        "ai_jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_shadow_evaluation_runs_raw_message_id",
        "shadow_evaluation_runs",
        "raw_messages",
        ["raw_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_shadow_evaluation_runs_job_id", "shadow_evaluation_runs", ["job_id"])
    op.create_index(
        "ix_shadow_evaluation_runs_raw_message_id",
        "shadow_evaluation_runs",
        ["raw_message_id"],
    )
    op.create_unique_constraint(
        "uq_shadow_evaluation_runs_job_id", "shadow_evaluation_runs", ["job_id"]
    )
    op.create_unique_constraint(
        "uq_shadow_evaluation_runs_source_revision",
        "shadow_evaluation_runs",
        ["raw_message_id", "source_revision", "analysis_generation"],
    )


def downgrade():
    op.drop_constraint(
        "uq_shadow_evaluation_runs_source_revision",
        "shadow_evaluation_runs",
        type_="unique",
    )
    op.drop_constraint("uq_shadow_evaluation_runs_job_id", "shadow_evaluation_runs", type_="unique")
    op.drop_index("ix_shadow_evaluation_runs_raw_message_id", table_name="shadow_evaluation_runs")
    op.drop_index("ix_shadow_evaluation_runs_job_id", table_name="shadow_evaluation_runs")
    op.drop_constraint(
        "fk_shadow_evaluation_runs_raw_message_id",
        "shadow_evaluation_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_shadow_evaluation_runs_job_id", "shadow_evaluation_runs", type_="foreignkey"
    )
    for name in (
        "reserved_usd",
        "prompt_cache_hit_tokens",
        "last_error",
        "lease_until",
        "available_at",
        "attempts",
        "provider_payload_hashes",
        "validation_result_json",
        "context_result_json",
        "memory_json",
        "input_json",
        "source_revision",
        "analysis_generation",
        "raw_message_id",
        "job_id",
    ):
        op.drop_column("shadow_evaluation_runs", name)
