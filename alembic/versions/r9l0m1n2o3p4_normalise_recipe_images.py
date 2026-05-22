"""normalise recipe images into a row-per-image table

Replaces the parallel ARRAY(String) columns recipes.image_urls and
recipes.thumbnail_urls with a normalised recipe_images table (one row per
image, with explicit position).

Revision ID: r9l0m1n2o3p4
Revises: q8k9l0m1n2o3
Create Date: 2026-05-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "r9l0m1n2o3p4"
down_revision = "q8k9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "recipe_id",
            sa.Integer(),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_url", sa.String(length=1024), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=1024), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_recipe_images_recipe_position", "recipe_images", ["recipe_id", "position"]
    )

    # Backfill: unnest the parallel arrays in lock-step. We use WITH ORDINALITY
    # on each unnest and JOIN by ordinality so the i-th element of image_urls
    # pairs up with the i-th element of thumbnail_urls. We also COALESCE on
    # the thumbnail side so a missing thumb (shorter array) falls back to the
    # full URL, since the column is NOT NULL.
    op.execute(
        """
        INSERT INTO recipe_images (recipe_id, full_url, thumbnail_url, position)
        SELECT
            r.id,
            f.full_url,
            COALESCE(t.thumb_url, f.full_url),
            (f.idx - 1)::int AS position
        FROM recipes r
        CROSS JOIN LATERAL unnest(r.image_urls)
            WITH ORDINALITY AS f(full_url, idx)
        LEFT JOIN LATERAL unnest(r.thumbnail_urls)
            WITH ORDINALITY AS t(thumb_url, idx) ON t.idx = f.idx
        WHERE r.image_urls IS NOT NULL AND array_length(r.image_urls, 1) > 0
        """
    )

    op.drop_column("recipes", "image_urls")
    op.drop_column("recipes", "thumbnail_urls")


def downgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column(
            "image_urls",
            sa.dialects.postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "recipes",
        sa.Column(
            "thumbnail_urls",
            sa.dialects.postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )

    op.execute(
        """
        UPDATE recipes r
        SET image_urls = sub.full_urls,
            thumbnail_urls = sub.thumb_urls
        FROM (
            SELECT
                recipe_id,
                array_agg(full_url ORDER BY position) AS full_urls,
                array_agg(thumbnail_url ORDER BY position) AS thumb_urls
            FROM recipe_images
            GROUP BY recipe_id
        ) AS sub
        WHERE r.id = sub.recipe_id
        """
    )

    op.drop_index("ix_recipe_images_recipe_position", table_name="recipe_images")
    op.drop_table("recipe_images")
