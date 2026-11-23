"""Manually verified RUB payment links for group access.

Revision ID: 0068
Revises: 0067
"""

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "group_bank_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("payer_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount_rub", sa.Integer(), nullable=False),
        sa.Column("payment_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", sa.BigInteger()),
        sa.Column("cleanup_at", sa.DateTime(timezone=True)),
        sa.Column("cleanup_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("months BETWEEN 1 AND 4", name="ck_group_bank_orders_months_1_4"),
        sa.CheckConstraint("amount_rub > 0", name="ck_group_bank_orders_positive_amount"),
    )
    op.create_index("ix_group_bank_orders_group_id", "group_bank_orders", ["group_id"])
    op.execute("ALTER TABLE group_bank_orders ENABLE ROW LEVEL SECURITY")
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON group_bank_orders FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON group_bank_orders FROM authenticated;
        END IF;
        END $$;"""
    )


def downgrade() -> None:
    op.drop_index("ix_group_bank_orders_group_id", table_name="group_bank_orders")
    op.drop_table("group_bank_orders")
