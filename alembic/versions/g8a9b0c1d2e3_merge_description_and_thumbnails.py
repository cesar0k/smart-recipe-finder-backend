"""merge description and thumbnails heads

Revision ID: g8a9b0c1d2e3
Revises: e6f7a8b9c0d1, f7a8b9c0d1e2
Create Date: 2026-04-15 16:00:00.000000

"""

from typing import Union


# revision identifiers, used by Alembic.
revision: str = "g8a9b0c1d2e3"
down_revision: Union[tuple[str, ...], None] = ("e6f7a8b9c0d1", "f7a8b9c0d1e2")
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
