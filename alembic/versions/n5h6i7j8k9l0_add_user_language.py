"""add language field to users

Revision ID: n5h6i7j8k9l0
Revises: m4g5h6i7j8k9
Create Date: 2026-05-15 22:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "n5h6i7j8k9l0"
down_revision: Union[str, None] = "m4g5h6i7j8k9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "language",
            sa.String(5),
            server_default=sa.text("'ru'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "language")
