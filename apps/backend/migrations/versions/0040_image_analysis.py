"""Durable versioned image passes and per-call accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "image_analysis_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "attachment_id",
            sa.Uuid(),
            sa.ForeignKey("message_attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("pass_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("error", sa.String(100)),
        sa.Column("result_json", sa.Text()),
        sa.Column("pixel_sha256", sa.String(64)),
        sa.Column("visual_hash", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attachment_id", "source_revision", "context_sha256", "pass_number"),
        sa.CheckConstraint("pass_number IN (1, 2)"),
    )
    for column in ("group_id", "attachment_id", "raw_message_id", "state", "delete_at"):
        op.create_index(f"ix_image_analysis_jobs_{column}", "image_analysis_jobs", [column])
    op.create_table(
        "image_analysis_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("image_analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("claim_token", sa.Uuid(), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("charged_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("job_id", "group_id", "created_at"):
        op.create_index(f"ix_image_analysis_calls_{column}", "image_analysis_calls", [column])


def downgrade():
    op.drop_table("image_analysis_calls")
    op.drop_table("image_analysis_jobs")
