"""Link canonical academic items to backward-compatible product rows."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("homework_raw_message_id_key", "homework", type_="unique")
    op.create_index(
        "ix_homework_raw_message_id", "homework", ["raw_message_id"], unique=False
    )
    op.create_table(
        "canonical_projection_links",
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_academic_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("projected_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "entity_type", "entity_id", name="uq_canonical_projection_entity"
        ),
    )


def downgrade():
    op.drop_table("canonical_projection_links")
    op.drop_index("ix_homework_raw_message_id", table_name="homework")
    op.create_unique_constraint(
        "homework_raw_message_id_key", "homework", ["raw_message_id"]
    )
