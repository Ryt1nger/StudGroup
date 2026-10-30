"""One funded release allowance shared by text, vision, reservations and retries."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("release_budget_usd", sa.Numeric(12, 8)),
        ("release_budget_since", sa.DateTime(timezone=True)),
        ("release_budget_until", sa.DateTime(timezone=True)),
        ("release_budget_receipt", sa.String(64)),
        ("paid_outcome_hold", sa.String(64)),
    ]:
        op.add_column("group_analysis_settings", sa.Column(name, kind, nullable=True))
    op.create_table(
        "analysis_budget_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("group_analysis_settings.group_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("charged_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("delete_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reserved_usd >= 0 AND charged_usd >= 0", name="analysis_budget_nonnegative"
        ),
    )
    op.create_index("ix_analysis_budget_calls_group_id", "analysis_budget_calls", ["group_id"])
    op.create_index("ix_analysis_budget_calls_delete_at", "analysis_budget_calls", ["delete_at"])


def downgrade():
    op.drop_table("analysis_budget_calls")
    for name in [
        "paid_outcome_hold",
        "release_budget_receipt",
        "release_budget_until",
        "release_budget_since",
        "release_budget_usd",
    ]:
        op.drop_column("group_analysis_settings", name)
