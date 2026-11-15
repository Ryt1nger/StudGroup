"""Expiring private manual CAPTCHA bridge for owner-only live browser handoff."""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ranepa_manual_captcha",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("ranepa_crawl_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("screenshot_base64", sa.Text()),
        sa.Column("left_image_base64", sa.Text()),
        sa.Column("right_image_base64", sa.Text()),
        sa.Column("angle_left", sa.Integer()),
        sa.Column("angle_right", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('pending', 'answered', 'consumed', 'expired')",
            name="ck_ranepa_manual_captcha_state",
        ),
        sa.CheckConstraint(
            "angle_left IS NULL OR angle_left BETWEEN 0 AND 359",
            name="ck_ranepa_manual_captcha_left",
        ),
        sa.CheckConstraint(
            "angle_right IS NULL OR angle_right BETWEEN 0 AND 359",
            name="ck_ranepa_manual_captcha_right",
        ),
    )
    op.create_index(
        "ix_ranepa_manual_captcha_job",
        "ranepa_manual_captcha",
        ["job_id", "attempt"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ranepa_manual_captcha ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_ranepa_manual_captcha_job", table_name="ranepa_manual_captcha")
    op.drop_table("ranepa_manual_captcha")
