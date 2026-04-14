"""add thumbnail_urls to recipes

Revision ID: f7a8b9c0d1e2
Revises: d5e6f7a8b9c0
Create Date: 2026-04-14 10:00:00.000000

"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("thumbnail_urls", ARRAY(sa.String), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("recipes", "thumbnail_urls")
