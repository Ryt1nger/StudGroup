"""Source-retained local visual coverage proof; no changes to private-source lifetime."""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("image_analysis_jobs", sa.Column("visual_coverage_json", sa.Text()))


def downgrade():
    op.drop_column("image_analysis_jobs", "visual_coverage_json")
