"""Pinned provider/model identity and source-bounded image reading reuse."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "image_analysis_jobs",
        sa.Column("model_key", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "image_analysis_jobs",
        sa.Column(
            "reused_from_job_id",
            sa.Uuid(),
            sa.ForeignKey(
                "image_analysis_jobs.id", ondelete="SET NULL", name="fk_image_reading_reuse"
            ),
        ),
    )


def downgrade():
    op.drop_column("image_analysis_jobs", "reused_from_job_id")
    op.drop_column("image_analysis_jobs", "model_key")
