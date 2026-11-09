"""Encrypt recoverable shared Telegram invitations for headman re-sharing.

Historical invitations only stored SHA-256 hashes. Their raw tokens cannot be
recovered; preserve them without forced revocation or bogus placeholder links.
"""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("group_invitations", sa.Column("token_ciphertext", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("group_invitations", "token_ciphertext")
