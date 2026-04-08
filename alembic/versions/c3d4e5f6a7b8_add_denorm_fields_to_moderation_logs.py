"""add denormalized recipe_title and moderator_username to moderation_logs

Revision ID: c3d4e5f6a7b8
Revises: a8459ad75d76
Create Date: 2026-04-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a8459ad75d76"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "moderation_logs",
        sa.Column("recipe_title", sa.String(255), nullable=True),
    )
    op.add_column(
        "moderation_logs",
        sa.Column("moderator_username", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("moderation_logs", "moderator_username")
    op.drop_column("moderation_logs", "recipe_title")
