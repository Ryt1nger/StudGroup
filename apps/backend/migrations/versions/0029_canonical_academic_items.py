"""Add canonical academic items with revisioned field evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "canonical_academic_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("destination", sa.String(16), nullable=False),
        sa.Column("semantic_kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255)),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_date_only", sa.Boolean(), nullable=False),
        sa.Column("deadline_basis", sa.String(32), nullable=False),
        sa.Column("audience_scope", sa.String(24), nullable=False),
        sa.Column("audience_label", sa.String(255)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("attention_reasons", sa.Text(), nullable=False),
        sa.Column("manual_lock", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_canonical_academic_items_group_id", "canonical_academic_items", ["group_id"]
    )
    op.create_index(
        "ix_canonical_academic_items_active",
        "canonical_academic_items",
        ["group_id", "state", "destination"],
    )
    op.create_table(
        "canonical_item_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_academic_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column(
            "raw_message_id",
            sa.Uuid(),
            sa.ForeignKey("raw_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("fact_path", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "item_id",
            "item_revision",
            "raw_message_id",
            "source_revision",
            "fact_path",
            name="uq_canonical_item_field_evidence",
        ),
    )
    op.create_index("ix_canonical_item_evidence_item_id", "canonical_item_evidence", ["item_id"])
    op.create_index(
        "ix_canonical_item_evidence_raw_message_id", "canonical_item_evidence", ["raw_message_id"]
    )
    op.create_table(
        "canonical_item_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_academic_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(24), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("item_id", "item_revision", name="uq_canonical_item_revision"),
    )
    op.create_index("ix_canonical_item_revisions_item_id", "canonical_item_revisions", ["item_id"])


def downgrade():
    op.drop_index("ix_canonical_item_revisions_item_id", table_name="canonical_item_revisions")
    op.drop_table("canonical_item_revisions")
    op.drop_index("ix_canonical_item_evidence_raw_message_id", table_name="canonical_item_evidence")
    op.drop_index("ix_canonical_item_evidence_item_id", table_name="canonical_item_evidence")
    op.drop_table("canonical_item_evidence")
    op.drop_index("ix_canonical_academic_items_active", table_name="canonical_academic_items")
    op.drop_index("ix_canonical_academic_items_group_id", table_name="canonical_academic_items")
    op.drop_table("canonical_academic_items")
