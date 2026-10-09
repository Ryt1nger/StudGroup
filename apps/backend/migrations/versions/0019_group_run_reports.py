"""Group-slot run envelopes; suppress undelivered individual-stage reports only."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("targets", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(24)),
        sa.UniqueConstraint("group_id", "slot"),
    )
    op.create_index("ix_ai_runs_group_id", "ai_runs", ["group_id"])
    outbox = sa.table(
        "bot_outbox", sa.column("state", sa.String()), sa.column("dedup_key", sa.String())
    )
    op.get_bind().execute(
        outbox.update()
        .where(
            outbox.c.state == "pending",
            sa.or_(
                *(
                    outbox.c.dedup_key.like(f"owner-test:%:{prefix}:%")
                    for prefix in (
                        "run-start",
                        "run-finish",
                        "run-reconciled",
                        "filter-start",
                        "filter-finish",
                    )
                )
            ),
        )
        .values(state="superseded")
    )


def downgrade():
    op.drop_table("ai_runs")
