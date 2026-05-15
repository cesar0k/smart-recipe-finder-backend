"""add pending_email fields for email change confirmation

Revision ID: l3f4g5h6i7j8
Revises: k2e3f4g5h6i7
Create Date: 2026-05-15 18:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "l3f4g5h6i7j8"
down_revision: Union[str, None] = "k2e3f4g5h6i7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # pending_email: new email address waiting for confirmation
    op.add_column(
        "users",
        sa.Column("pending_email", sa.String(255), nullable=True),
    )
    # pending_email_token: hashed token sent to the new address
    op.add_column(
        "users",
        sa.Column("pending_email_token", sa.String(128), nullable=True),
    )
    # reuse email_verification_sent_at as the sent timestamp for pending email too
    # (already exists from k2e3f4g5h6i7)


def downgrade() -> None:
    op.drop_column("users", "pending_email_token")
    op.drop_column("users", "pending_email")
