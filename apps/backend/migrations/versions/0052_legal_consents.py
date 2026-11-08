"""Versioned personal-data consent evidence and optional SDO/LK setup state."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("sdu_setup_skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "legal_consent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("document_version", sa.String(32), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_legal_consent_events_user_id", "legal_consent_events", ["user_id"])
    op.create_index("ix_legal_consent_events_purpose", "legal_consent_events", ["purpose"])


def downgrade():
    op.drop_index("ix_legal_consent_events_purpose", table_name="legal_consent_events")
    op.drop_index("ix_legal_consent_events_user_id", table_name="legal_consent_events")
    op.drop_table("legal_consent_events")
    op.drop_column("user_profiles", "sdu_setup_skipped_at")
