"""Persistent AI circuit breaker and spend guardrails."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ai_control",
        sa.Column("provider_failure_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_control", sa.Column("provider_circuit_open_until", sa.DateTime(timezone=True))
    )
    op.add_column("ai_control", sa.Column("provider_circuit_reason", sa.String(64)))


def downgrade():
    op.drop_column("ai_control", "provider_circuit_reason")
    op.drop_column("ai_control", "provider_circuit_open_until")
    op.drop_column("ai_control", "provider_failure_streak")
