"""Add auth tables and recipe moderation

Revision ID: a1b2c3d4e5f6
Revises: 041640134cb5
Create Date: 2026-04-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "041640134cb5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, index=True, nullable=False),
        sa.Column("username", sa.String(100), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token", sa.String(255), unique=True, index=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "recipe_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "recipe_id",
            sa.Integer(),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("instructions", sa.String(50000), nullable=False),
        sa.Column("cooking_time_in_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(50), nullable=False),
        sa.Column("cuisine", sa.String(50), nullable=True),
        sa.Column("ingredients", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Add auth/moderation columns to recipes
    op.add_column(
        "recipes",
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "recipes",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="approved",
        ),
    )
    op.add_column(
        "recipes",
        sa.Column("rejection_reason", sa.String(1000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipes", "rejection_reason")
    op.drop_column("recipes", "status")
    op.drop_column("recipes", "owner_id")
    op.drop_table("recipe_drafts")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
