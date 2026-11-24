"""Owner-managed payment buttons.

Revision ID: 0069
Revises: 0068
"""

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_payment_options",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False, unique=True),
        sa.Column("payment_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("months BETWEEN 1 AND 4", name="billing_option_months_1_4"),
    )
    op.execute("ALTER TABLE billing_payment_options ENABLE ROW LEVEL SECURITY")
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON billing_payment_options FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON billing_payment_options FROM authenticated;
        END IF;
        END $$;"""
    )


def downgrade() -> None:
    op.drop_table("billing_payment_options")
