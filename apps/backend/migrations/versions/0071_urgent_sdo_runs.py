"""Durable urgent SDO run and identity-only account targets.

Revision ID: 0071
Revises: 0070
"""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "urgent_sdo_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("found_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "urgent_sdo_targets",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("urgent_sdo_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("ranepa_crawl_jobs.id", ondelete="SET NULL")),
        sa.Column("baseline_attempts", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_urgent_sdo_targets_job_id", "urgent_sdo_targets", ["job_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("urgent_sdo_runs", "urgent_sdo_targets"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON {table} FROM anon;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    REVOKE ALL ON {table} FROM authenticated;
                END IF;
                END $$;"""
            )


def downgrade() -> None:
    op.drop_table("urgent_sdo_targets")
    op.drop_table("urgent_sdo_runs")
