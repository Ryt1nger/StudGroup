"""Opaque pointer to the separately stored RANEPA credential."""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_profiles", sa.Column("sdu_credential_id", sa.Uuid(), nullable=True))


def downgrade():
    op.drop_column("user_profiles", "sdu_credential_id")
