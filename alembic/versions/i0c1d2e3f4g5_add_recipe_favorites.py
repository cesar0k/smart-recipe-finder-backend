"""add recipe_favorites table and recipes.favorites_count

Revision ID: i0c1d2e3f4g5
Revises: h9b0c1d2e3f4
Create Date: 2026-05-10 21:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "i0c1d2e3f4g5"
down_revision: Union[str, None] = "h9b0c1d2e3f4"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_favorites",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "recipe_id"),
    )
    op.create_index(
        "ix_recipe_favorites_user_created",
        "recipe_favorites",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_recipe_favorites_recipe_id",
        "recipe_favorites",
        ["recipe_id"],
    )

    op.add_column(
        "recipes",
        sa.Column(
            "favorites_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("recipes", "favorites_count")
    op.drop_index("ix_recipe_favorites_recipe_id", table_name="recipe_favorites")
    op.drop_index("ix_recipe_favorites_user_created", table_name="recipe_favorites")
    op.drop_table("recipe_favorites")
