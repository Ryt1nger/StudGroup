"""Group-level trial, paid entitlement, and verified Telegram Stars orders.

Revision ID: 0067
Revises: 0066
"""

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "group_subscriptions",
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_until", sa.DateTime(timezone=True)),
        sa.Column("trial_warning_at", sa.DateTime(timezone=True)),
        sa.Column("trial_expired_notice_at", sa.DateTime(timezone=True)),
        sa.Column("renewal_warning_for", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "group_payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payer_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("invoice_url", sa.Text()),
        sa.Column("telegram_charge_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("refunded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("months BETWEEN 1 AND 4", name="ck_group_payment_orders_months_1_4"),
        sa.CheckConstraint(
            "amount_rub > 0 AND stars_amount > 0",
            name="ck_group_payment_orders_positive_amounts",
        ),
        sa.UniqueConstraint("telegram_charge_id", name="uq_group_payment_orders_telegram_charge_id"),
    )
    op.create_index("ix_group_payment_orders_group_id", "group_payment_orders", ["group_id"])
    # Public is part of Supabase's Data API exposed schemas. Never grant anon access to
    # monetary records; defense-in-depth RLS denies all direct client-side access.
    op.execute("ALTER TABLE group_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE group_payment_orders ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON group_subscriptions, group_payment_orders FROM anon, authenticated")


def downgrade() -> None:
    op.drop_index("ix_group_payment_orders_group_id", table_name="group_payment_orders")
    op.drop_table("group_payment_orders")
    op.drop_table("group_subscriptions")
