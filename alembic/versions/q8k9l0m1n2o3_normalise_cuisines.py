"""normalise cuisines into a reference table

Splits the free-form `recipes.cuisine` (VARCHAR) into:
  * `cuisines(id, name UNIQUE)` — reference table populated from existing
    DISTINCT values.
  * `recipes.cuisine_id` (FK → cuisines.id ON DELETE SET NULL).

RecipeDraft keeps its plain `cuisine` string column — drafts are snapshots
of proposed edits at a point in time, and dragging snapshots through a
shared reference table would muddle their semantics.

Revision ID: q8k9l0m1n2o3
Revises: p7j8k9l0m1n2
Create Date: 2026-05-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "q8k9l0m1n2o3"
down_revision = "p7j8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cuisines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("name", name="uq_cuisines_name"),
    )
    op.create_index("ix_cuisines_name", "cuisines", ["name"])

    # Populate cuisines from distinct, non-empty values
    op.execute(
        """
        INSERT INTO cuisines (name)
        SELECT DISTINCT trim(cuisine)
        FROM recipes
        WHERE cuisine IS NOT NULL AND length(trim(cuisine)) > 0
        """
    )

    # Add FK column, backfill, drop old string column
    op.add_column(
        "recipes",
        sa.Column(
            "cuisine_id",
            sa.Integer(),
            sa.ForeignKey("cuisines.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_recipes_cuisine_id", "recipes", ["cuisine_id"])

    op.execute(
        """
        UPDATE recipes r
        SET cuisine_id = c.id
        FROM cuisines c
        WHERE trim(r.cuisine) = c.name
        """
    )

    op.drop_column("recipes", "cuisine")


def downgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("cuisine", sa.String(length=50), nullable=True),
    )

    op.execute(
        """
        UPDATE recipes r
        SET cuisine = c.name
        FROM cuisines c
        WHERE r.cuisine_id = c.id
        """
    )

    op.drop_index("ix_recipes_cuisine_id", table_name="recipes")
    op.drop_column("recipes", "cuisine_id")

    op.drop_index("ix_cuisines_name", table_name="cuisines")
    op.drop_table("cuisines")
