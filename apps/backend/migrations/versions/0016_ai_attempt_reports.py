"""Durable owner-only analysis attempt lifecycle metrics; no historical replay."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_attempts", sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.add_column("ai_attempts", sa.Column("outcome", sa.String(24)))
    op.add_column("ai_attempts", sa.Column("metrics", sa.Text()))


def downgrade():
    op.drop_column("ai_attempts", "metrics")
    op.drop_column("ai_attempts", "outcome")
    op.drop_column("ai_attempts", "finished_at")
