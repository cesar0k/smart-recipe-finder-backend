"""normalise ingredients into reference + m2m

Splits the free-form ``recipes.ingredients`` JSONB array into:
  * ``ingredients(id, name UNIQUE)`` — reference table.
  * ``recipe_ingredients(recipe_id, ingredient_id, amount, unit, position)``
    — M2M with display-order and per-row amount/unit.

Existing JSONB entries are expected to look like ``{"name": "..."}`` (no
amount/unit yet); the migration is tolerant of either case.

``recipe_drafts.ingredients`` stays JSONB on purpose — drafts are
point-in-time snapshots of proposed changes and shouldn't be threaded
through the shared reference table.

Revision ID: s0m1n2o3p4q5
Revises: r9l0m1n2o3p4
Create Date: 2026-05-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "s0m1n2o3p4q5"
down_revision = "r9l0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.UniqueConstraint("name", name="uq_ingredients_name"),
    )
    op.create_index("ix_ingredients_name", "ingredients", ["name"])

    op.create_table(
        "recipe_ingredients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "recipe_id",
            sa.Integer(),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ingredient_id",
            sa.Integer(),
            sa.ForeignKey("ingredients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.String(length=50), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("recipe_id", "ingredient_id", name="uq_recipe_ingredient"),
    )
    op.create_index(
        "ix_recipe_ingredients_recipe_position",
        "recipe_ingredients",
        ["recipe_id", "position"],
    )
    op.create_index(
        "ix_recipe_ingredients_ingredient", "recipe_ingredients", ["ingredient_id"]
    )

    # Populate ingredients from all distinct names seen in any recipe's JSONB.
    # Names are normalised: trim + lower so we don't get duplicates that only
    # differ in casing or whitespace.
    op.execute(
        """
        INSERT INTO ingredients (name)
        SELECT DISTINCT lower(trim(elem->>'name'))
        FROM recipes,
             LATERAL jsonb_array_elements(ingredients) AS elem
        WHERE elem->>'name' IS NOT NULL
          AND length(trim(elem->>'name')) > 0
        ON CONFLICT (name) DO NOTHING
        """
    )

    # Wire each recipe's JSONB list to ingredient rows via the M2M table.
    # ON CONFLICT defends against duplicate (recipe, ingredient) pairs in
    # the source JSONB — we take the first occurrence and ignore the rest.
    op.execute(
        """
        INSERT INTO recipe_ingredients
            (recipe_id, ingredient_id, amount, unit, position)
        SELECT
            r.id,
            i.id,
            NULLIF(elem->>'amount', ''),
            NULLIF(elem->>'unit', ''),
            (ord - 1)::int
        FROM recipes r,
             LATERAL jsonb_array_elements(r.ingredients)
                 WITH ORDINALITY AS t(elem, ord)
        JOIN ingredients i ON i.name = lower(trim(elem->>'name'))
        WHERE elem->>'name' IS NOT NULL
          AND length(trim(elem->>'name')) > 0
        ON CONFLICT (recipe_id, ingredient_id) DO NOTHING
        """
    )

    op.drop_column("recipes", "ingredients")


def downgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "ingredients",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.execute(
        """
        UPDATE recipes r
        SET ingredients = sub.payload
        FROM (
            SELECT
                ri.recipe_id,
                jsonb_agg(
                    jsonb_build_object(
                        'name', i.name,
                        'amount', ri.amount,
                        'unit', ri.unit
                    )
                    ORDER BY ri.position
                ) AS payload
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            GROUP BY ri.recipe_id
        ) AS sub
        WHERE r.id = sub.recipe_id
        """
    )

    op.drop_index("ix_recipe_ingredients_ingredient", table_name="recipe_ingredients")
    op.drop_index(
        "ix_recipe_ingredients_recipe_position", table_name="recipe_ingredients"
    )
    op.drop_table("recipe_ingredients")
    op.drop_index("ix_ingredients_name", table_name="ingredients")
    op.drop_table("ingredients")
