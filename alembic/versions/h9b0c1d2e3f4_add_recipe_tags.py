"""add recipe_tags table

Revision ID: h9b0c1d2e3f4
Revises: g8a9b0c1d2e3
Create Date: 2026-05-05 12:00:00.000000

"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h9b0c1d2e3f4"
down_revision: Union[str, None] = "g8a9b0c1d2e3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("vegetarian", sa.Boolean(), nullable=True),
        sa.Column("vegan", sa.Boolean(), nullable=True),
        sa.Column("gluten_free", sa.Boolean(), nullable=True),
        sa.Column("dairy_free", sa.Boolean(), nullable=True),
        sa.Column("meal_type", sa.String(length=20), nullable=True),
        sa.Column("main_protein", sa.String(length=20), nullable=True),
        sa.Column("cooking_method", sa.String(length=20), nullable=True),
        sa.Column("spice_level", sa.String(length=10), nullable=True),
        sa.Column("occasion", sa.String(length=20), nullable=True),
        sa.Column("cost_tier", sa.String(length=10), nullable=True),
        sa.Column("technique_difficulty", sa.String(length=15), nullable=True),
        sa.Column("cultural_sub_region", sa.String(length=100), nullable=True),
        sa.Column(
            "allergens",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id"),
    )
    op.create_index("ix_recipe_tags_recipe_id", "recipe_tags", ["recipe_id"])


def downgrade() -> None:
    op.drop_index("ix_recipe_tags_recipe_id", table_name="recipe_tags")
    op.drop_table("recipe_tags")
