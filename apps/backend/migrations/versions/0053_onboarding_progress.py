"""Persistent per-membership onboarding, preserving existing active pilot members."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "onboarding_progress",
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("step", sa.String(24), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Never force the already-connected pilot users (including BI 1.2) through
    # another onboarding. Only memberships created after rollout require setup.
    op.execute(
        """
        INSERT INTO onboarding_progress (membership_id, step, completed_at, updated_at)
        SELECT id, 'complete', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
          FROM memberships
         WHERE status = 'active'
        """
    )


def downgrade():
    op.drop_table("onboarding_progress")
