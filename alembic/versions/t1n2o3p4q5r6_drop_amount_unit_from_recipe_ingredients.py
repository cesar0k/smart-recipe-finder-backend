"""drop amount and unit from recipe_ingredients

The columns were created in ``s0m1n2o3p4q5_normalise_ingredients`` as a
forward-looking provision for structured quantities, but the feature was
never implemented: the UI submits each ingredient as a single free-form
string (e.g. "½ стакана сахара", "щепотка соли", "по вкусу") that lands
directly in ``Ingredient.name``. The columns have always been NULL, so
dropping them costs nothing and removes dead schema.

Revision ID: t1n2o3p4q5r6
Revises: s0m1n2o3p4q5
Create Date: 2026-05-23 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "t1n2o3p4q5r6"
down_revision = "s0m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("recipe_ingredients", "amount")
    op.drop_column("recipe_ingredients", "unit")


def downgrade() -> None:
    op.add_column(
        "recipe_ingredients",
        sa.Column("amount", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "recipe_ingredients",
        sa.Column("unit", sa.String(length=50), nullable=True),
    )
