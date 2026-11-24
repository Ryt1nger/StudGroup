"""Durable background process control; null desired state inherits existing configuration.

Revision ID: 0070
Revises: 0069
"""

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "background_processes",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_generation", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("error_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_stage", sa.String(32)),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "name IN ('ai', 'sdo', 'telegram_outbox', 'scheduler')",
            name="background_process_name",
        ),
    )
    op.bulk_insert(
        table,
        [{"name": name} for name in ("ai", "sdo", "telegram_outbox", "scheduler")],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE background_processes ENABLE ROW LEVEL SECURITY")
        op.execute(
            """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                REVOKE ALL ON background_processes FROM anon;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                REVOKE ALL ON background_processes FROM authenticated;
            END IF;
            END $$;"""
        )


def downgrade() -> None:
    op.drop_table("background_processes")
