"""Headman sessions, decisions, audit, invitations and manually maintained group data."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def group_column():
    return sa.Column(
        "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )


def upgrade():
    op.add_column(
        "ai_candidates", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "schedule_patterns", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("schedule_patterns", sa.Column("online_url", sa.String(2048)))
    op.add_column(
        "schedule_patterns",
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "academic_deadlines",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("academic_deadlines", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column(
        "bot_admin_sessions",
        sa.Column("candidate_role", sa.String(16), nullable=False, server_default="student"),
    )
    op.add_column("bot_outbox", sa.Column("dedup_key", sa.String(200)))
    op.create_unique_constraint("uq_bot_outbox_dedup_key", "bot_outbox", ["dedup_key"])
    op.create_table(
        "headman_sessions",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE")),
        sa.Column("nonce", sa.String(24), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "headman_decisions",
        sa.Column("entity_key", sa.String(100), primary_key=True),
        group_column(),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("deferred_until", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "headman_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        group_column(),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("entity_key", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("before", sa.Text(), nullable=False),
        sa.Column("after", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "headman_settings",
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("questions_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_times", sa.String(32), nullable=False),
    )
    op.create_table(
        "group_invitations",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        group_column(),
        sa.Column(
            "issuer_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("used_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_table(
        "schedule_exceptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        group_column(),
        sa.Column(
            "pattern_id",
            sa.Uuid(),
            sa.ForeignKey("schedule_patterns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(512)),
        sa.Column("online_url", sa.String(2048)),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("pattern_id", "occurrence_date"),
    )
    op.create_table(
        "material_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        group_column(),
        sa.Column("entity_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
    )

    op.create_table(
        "headman_question_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        group_column(),
        sa.Column("entity_key", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("entity_key", "revision"),
    )


def downgrade():
    op.drop_column("ai_candidates", "revision")
    for name in [
        "headman_question_deliveries",
        "material_links",
        "schedule_exceptions",
        "group_invitations",
        "headman_settings",
        "headman_audit",
        "headman_decisions",
        "headman_sessions",
    ]:
        op.drop_table(name)
    op.drop_constraint("uq_bot_outbox_dedup_key", "bot_outbox", type_="unique")
    op.drop_column("bot_outbox", "dedup_key")
    op.drop_column("bot_admin_sessions", "candidate_role")
    for table, columns in [
        ("academic_deadlines", ["cancelled_at", "revision"]),
        ("schedule_patterns", ["cancelled", "online_url", "revision"]),
    ]:
        for name in columns:
            op.drop_column(table, name)
