"""Add per-group analysis routing and bounded shadow evaluation records."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_analysis_settings",
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("analysis_mode", sa.String(16), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("shadow_daily_budget_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("shadow_sample_percent", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "analysis_mode IN ('legacy', 'shadow', 'new')",
            name="group_analysis_mode",
        ),
        sa.CheckConstraint(
            "shadow_sample_percent >= 0 AND shadow_sample_percent <= 100",
            name="shadow_sample_percent_range",
        ),
    )
    op.create_table(
        "shadow_evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("analysis_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("metrics_json", sa.Text()),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("charged_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shadow_evaluation_runs_group_id", "shadow_evaluation_runs", ["group_id"])
    op.create_index("ix_shadow_evaluation_runs_batch_id", "shadow_evaluation_runs", ["batch_id"])
    op.create_index("ix_shadow_evaluation_runs_delete_at", "shadow_evaluation_runs", ["delete_at"])


def downgrade():
    op.drop_index("ix_shadow_evaluation_runs_delete_at", table_name="shadow_evaluation_runs")
    op.drop_index("ix_shadow_evaluation_runs_batch_id", table_name="shadow_evaluation_runs")
    op.drop_index("ix_shadow_evaluation_runs_group_id", table_name="shadow_evaluation_runs")
    op.drop_table("shadow_evaluation_runs")
    op.drop_table("group_analysis_settings")
