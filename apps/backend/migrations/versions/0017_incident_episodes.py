"""Numbered incident episodes, occurrence times and separate durable lifecycle notices."""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("owner_incidents", sa.Column("current_episode_id", sa.Integer()))
    op.create_table(
        "owner_incident_episodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "code",
            sa.String(64),
            sa.ForeignKey("owner_incidents.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("first_detail", sa.String(64)),
        sa.Column("last_detail", sa.String(64)),
        sa.Column("opening_pending", sa.Boolean(), nullable=False),
        sa.Column("recovery_pending", sa.Boolean(), nullable=False),
        sa.Column("opening_notified_at", sa.DateTime(timezone=True)),
        sa.Column("recovery_notified_at", sa.DateTime(timezone=True)),
        sa.Column("legacy", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_owner_incident_episodes_code", "owner_incident_episodes", ["code"])
    connection = op.get_bind()
    episodes = sa.table(
        "owner_incident_episodes",
        sa.column("id", sa.Integer()),
        sa.column("code"),
        sa.column("opened_at", sa.DateTime(timezone=True)),
        sa.column("last_seen_at", sa.DateTime(timezone=True)),
        sa.column("recovered_at", sa.DateTime(timezone=True)),
        sa.column("occurrences"),
        sa.column("opening_pending"),
        sa.column("recovery_pending"),
        sa.column("legacy"),
    )
    for row in (
        connection.execute(
            sa.text(
                "SELECT code, active, opened_at, recovered_at, notification_pending FROM owner_incidents ORDER BY opened_at, code"
            )
        )
        .mappings()
        .all()
    ):
        opened = (
            datetime.fromisoformat(row["opened_at"])
            if isinstance(row["opened_at"], str)
            else row["opened_at"]
        )
        recovered = (
            datetime.fromisoformat(row["recovered_at"])
            if isinstance(row["recovered_at"], str)
            else row["recovered_at"]
        )
        number = connection.execute(
            episodes.insert()
            .values(
                code=row["code"],
                opened_at=opened,
                last_seen_at=opened,
                recovered_at=recovered,
                occurrences=1,
                opening_pending=bool(row["active"] and row["notification_pending"]),
                recovery_pending=bool(not row["active"] and row["notification_pending"]),
                legacy=True,
            )
            .returning(episodes.c.id)
        ).scalar_one()
        connection.execute(
            sa.text(
                "UPDATE owner_incidents SET current_episode_id=:number, notification_pending=false WHERE code=:code"
            ),
            {"number": number, "code": row["code"]},
        )
        # Operational metadata only; never message text, credentials or provider bodies.
        print(
            f"INCIDENT_BACKFILL number={number} code={row['code']} opened_at={row['opened_at']} recovered_at={row['recovered_at']} pending={bool(row['notification_pending'])}"
        )


def downgrade():
    op.drop_index("ix_owner_incident_episodes_code", table_name="owner_incident_episodes")
    op.drop_table("owner_incident_episodes")
    op.drop_column("owner_incidents", "current_episode_id")
