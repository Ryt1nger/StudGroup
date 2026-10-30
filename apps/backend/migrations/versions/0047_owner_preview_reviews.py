"""Private feedback for owner shadow previews."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "owner_preview_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("wave_key", sa.String(64), nullable=False),
        sa.Column("card_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_owner_preview_reviews_group_wave", "owner_preview_reviews", ["group_id", "wave_key"])


def downgrade():
    op.drop_index("ix_owner_preview_reviews_group_wave", table_name="owner_preview_reviews")
    op.drop_table("owner_preview_reviews")
