"""add ratings, comments, comment_reports, and engagement fields

Revision ID: j1d2e3f4g5h6
Revises: i0c1d2e3f4g5
Create Date: 2026-05-14 12:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "j1d2e3f4g5h6"
down_revision: Union[str, None] = "i0c1d2e3f4g5"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # ── recipe_ratings ────────────────────────────────────────────────────────
    op.create_table(
        "recipe_ratings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "recipe_id", name="uq_recipe_ratings_user_recipe"),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_recipe_ratings_range"),
    )
    op.create_index("ix_recipe_ratings_recipe_id", "recipe_ratings", ["recipe_id"])
    op.create_index("ix_recipe_ratings_user_id", "recipe_ratings", ["user_id"])

    # ── recipe_comments ───────────────────────────────────────────────────────
    op.create_table(
        "recipe_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_comment_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.String(2000), nullable=False),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"],
            ["recipe_comments.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_recipe_comments_recipe_created",
        "recipe_comments",
        ["recipe_id", "created_at"],
    )
    op.create_index("ix_recipe_comments_parent_id", "recipe_comments", ["parent_comment_id"])
    op.create_index("ix_recipe_comments_user_id", "recipe_comments", ["user_id"])

    # ── recipe_comment_reports ────────────────────────────────────────────────
    op.create_table(
        "recipe_comment_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["comment_id"], ["recipe_comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "comment_id", "reporter_id", name="uq_comment_reports_comment_reporter"
        ),
    )
    op.create_index("ix_comment_reports_comment_id", "recipe_comment_reports", ["comment_id"])
    op.create_index("ix_comment_reports_reporter_id", "recipe_comment_reports", ["reporter_id"])

    # ── notifications: comment_id for deep-linking to a specific comment ─────
    op.add_column(
        "notifications",
        sa.Column(
            "comment_id",
            sa.Integer(),
            sa.ForeignKey("recipe_comments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── recipes: new engagement columns ───────────────────────────────────────
    op.add_column(
        "recipes",
        sa.Column("average_rating", sa.Float(), server_default=sa.text("0.0"), nullable=False),
    )
    op.add_column(
        "recipes",
        sa.Column("ratings_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "recipes",
        sa.Column("comments_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "recipes",
        sa.Column(
            "engagement_score", sa.Float(), server_default=sa.text("0.0"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("notifications", "comment_id")
    op.drop_column("recipes", "engagement_score")
    op.drop_column("recipes", "comments_count")
    op.drop_column("recipes", "ratings_count")
    op.drop_column("recipes", "average_rating")

    op.drop_index("ix_comment_reports_reporter_id", table_name="recipe_comment_reports")
    op.drop_index("ix_comment_reports_comment_id", table_name="recipe_comment_reports")
    op.drop_table("recipe_comment_reports")

    op.drop_index("ix_recipe_comments_user_id", table_name="recipe_comments")
    op.drop_index("ix_recipe_comments_parent_id", table_name="recipe_comments")
    op.drop_index("ix_recipe_comments_recipe_created", table_name="recipe_comments")
    op.drop_table("recipe_comments")

    op.drop_index("ix_recipe_ratings_user_id", table_name="recipe_ratings")
    op.drop_index("ix_recipe_ratings_recipe_id", table_name="recipe_ratings")
    op.drop_table("recipe_ratings")
