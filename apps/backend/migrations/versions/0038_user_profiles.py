"""Per-user profile: name, contact, study platform (password encrypted) and notification preferences."""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("first_name", sa.String(60)),
        sa.Column("last_name", sa.String(60)),
        sa.Column("email", sa.String(120)),
        sa.Column("sdu_platform", sa.String(60)),
        sa.Column("sdu_url", sa.String(200)),
        sa.Column("sdu_login", sa.String(80)),
        sa.Column("sdu_calendar_url", sa.String(300)),
        sa.Column("sdu_password_encrypted", sa.Text()),
        sa.Column("settings", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("user_profiles")
