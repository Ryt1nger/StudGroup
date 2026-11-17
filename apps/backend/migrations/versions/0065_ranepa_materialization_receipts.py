"""Record full verified RANEPA source reads and atomic recipient delivery receipts.

Legacy rows remain safely unproven: no coverage receipts are backfilled.
Migration 0064 must already be applied; this migration must not run on
production without an explicit owner-approved release window.
"""

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ranepa_personal_snapshots",
        sa.Column("source_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ranepa_personal_snapshots",
        sa.Column("source_digest", sa.String(64), nullable=True),
    )
    op.create_table(
        "ranepa_materialization_receipts",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "recipient_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("week_start", sa.Date(), primary_key=True),
        sa.Column(
            "source_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_ranepa_materialization_receipts_recipient",
        "ranepa_materialization_receipts",
        ["group_id", "recipient_user_id", "week_start"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ranepa_materialization_receipts ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index(
        "ix_ranepa_materialization_receipts_recipient",
        table_name="ranepa_materialization_receipts",
    )
    op.drop_table("ranepa_materialization_receipts")
    op.drop_column("ranepa_personal_snapshots", "source_digest")
    op.drop_column("ranepa_personal_snapshots", "source_complete")
