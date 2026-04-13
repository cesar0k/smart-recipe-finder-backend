"""add description to recipes and drafts

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-13 16:00:00.000000

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("recipes", sa.Column("description", sa.String(2000), nullable=True))
    op.add_column("recipe_drafts", sa.Column("description", sa.String(2000), nullable=True))


def downgrade() -> None:
    op.drop_column("recipe_drafts", "description")
    op.drop_column("recipes", "description")
