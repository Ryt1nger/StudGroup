"""Add durable per-attempt and per-proposal AI outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_application_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("ai_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("ai_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("analysis_generation", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("proposal_kind", sa.String(24), nullable=False),
        sa.Column("proposal_ordinal", sa.Integer(), nullable=False),
        sa.Column("technical_status", sa.String(32), nullable=False),
        sa.Column("academic_status", sa.String(32)),
        sa.Column("application_status", sa.String(32)),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source_message_ids", sa.Text()),
        sa.Column("diagnostics", sa.Text()),
        sa.Column("provider_payload_sha256", sa.String(64)),
        sa.Column("raw_provider_response", sa.Text()),
        sa.Column("model", sa.String(64)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attempt_id",
            "stage",
            "proposal_kind",
            "proposal_ordinal",
            name="uq_analysis_outcome_attempt_proposal",
        ),
    )
    op.create_index(
        "ix_analysis_outcomes_attempt_id", "analysis_application_outcomes", ["attempt_id"]
    )
    op.create_index("ix_analysis_outcomes_job_id", "analysis_application_outcomes", ["job_id"])
    op.create_index("ix_analysis_outcomes_group_id", "analysis_application_outcomes", ["group_id"])
    op.create_index(
        "ix_analysis_outcomes_delete_at", "analysis_application_outcomes", ["delete_at"]
    )


def downgrade():
    op.drop_index("ix_analysis_outcomes_delete_at", table_name="analysis_application_outcomes")
    op.drop_index("ix_analysis_outcomes_group_id", table_name="analysis_application_outcomes")
    op.drop_index("ix_analysis_outcomes_job_id", table_name="analysis_application_outcomes")
    op.drop_index("ix_analysis_outcomes_attempt_id", table_name="analysis_application_outcomes")
    op.drop_table("analysis_application_outcomes")
