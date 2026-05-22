"""convert string columns to postgres enums

Replaces String(...) columns with native Postgres ENUM types for fields that
have a fixed, well-known value domain. The values come from `app.models.enums`
and were cross-checked against `SELECT DISTINCT` on the live database before
this migration was written.

Note: each `alter_column` uses `postgresql_using = "<col>::<enum>"` so Postgres
can cast the existing string values to the new enum type in place.

Revision ID: p7j8k9l0m1n2
Revises: o6i7j8k9l0m1
Create Date: 2026-05-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "p7j8k9l0m1n2"
down_revision = "o6i7j8k9l0m1"
branch_labels = None
depends_on = None


# (enum_name, values, [(table, column, server_default_or_None), ...])
# server_default is preserved across the type change — Postgres can't auto-cast
# a string default to the new enum, so we drop & re-set it explicitly.
ENUM_SPECS: list[tuple[str, tuple[str, ...], list[tuple[str, str, str | None]]]] = [
    ("user_role", ("user", "moderator", "admin"), [("users", "role", "user")]),
    ("auth_provider", ("local", "google"), [("users", "auth_provider", "local")]),
    ("user_language", ("ru", "en"), [("users", "language", "ru")]),
    ("recipe_difficulty", ("easy", "medium", "hard"), [("recipes", "difficulty", None)]),
    ("recipe_status", ("approved", "pending", "rejected"), [("recipes", "status", "approved")]),
    ("draft_status", ("pending", "approved", "rejected"), [("recipe_drafts", "status", "pending")]),
    (
        "notification_type",
        (
            "new_comment",
            "comment_reply",
            "comment_reported",
            "new_pending_recipe",
            "recipe_approved",
            "recipe_rejected",
            "draft_approved",
            "draft_rejected",
            "recipe_deleted",
            "user_followed",
            "followed_user_published",
        ),
        [("notifications", "type", None), ("email_notification_preferences", "type", None)],
    ),
    (
        "meal_type",
        (
            "breakfast",
            "lunch",
            "dinner",
            "dessert",
            "snack",
            "drink",
            "soup",
            "salad",
            "side",
            "other",
        ),
        [("recipe_tags", "meal_type", None)],
    ),
    (
        "main_protein",
        (
            "beef",
            "pork",
            "chicken",
            "fish",
            "seafood",
            "eggs",
            "legumes",
            "none",
        ),
        [("recipe_tags", "main_protein", None)],
    ),
    (
        "cooking_method",
        (
            "baked",
            "fried",
            "boiled",
            "stewed",
            "roasted",
            "raw",
            "no_cook",
            "slow_cooked",
            "other",
        ),
        [("recipe_tags", "cooking_method", None)],
    ),
    ("spice_level", ("none", "mild", "medium", "hot"), [("recipe_tags", "spice_level", None)]),
    (
        "occasion",
        (
            "everyday",
            "holiday",
            "party",
            "brunch",
            "picnic",
            "barbecue",
            "kids_friendly",
        ),
        [("recipe_tags", "occasion", None)],
    ),
    ("cost_tier", ("budget", "moderate", "premium"), [("recipe_tags", "cost_tier", None)]),
    (
        "technique_difficulty",
        ("basic", "intermediate", "advanced"),
        [("recipe_tags", "technique_difficulty", None)],
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    for enum_name, values, columns in ENUM_SPECS:
        pg_enum = postgresql.ENUM(*values, name=enum_name, create_type=False)
        pg_enum.create(bind, checkfirst=True)
        for table, column, server_default in columns:
            if server_default is not None:
                # Postgres can't cast the literal-string default to the new
                # enum type implicitly — drop it, cast the column, re-set it.
                op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
            op.alter_column(
                table,
                column,
                existing_type=sa.String(),
                type_=postgresql.ENUM(*values, name=enum_name, create_type=False),
                postgresql_using=f"{column}::{enum_name}",
            )
            if server_default is not None:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"SET DEFAULT '{server_default}'::{enum_name}"
                )


def downgrade() -> None:
    bind = op.get_bind()
    for enum_name, values, columns in ENUM_SPECS:
        for table, column, server_default in columns:
            if server_default is not None:
                op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
            op.alter_column(
                table,
                column,
                existing_type=postgresql.ENUM(*values, name=enum_name, create_type=False),
                type_=sa.String(),
                postgresql_using=f"{column}::text",
            )
            if server_default is not None:
                op.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "
                    f"SET DEFAULT '{server_default}'"
                )
        pg_enum = postgresql.ENUM(*values, name=enum_name, create_type=False)
        pg_enum.drop(bind, checkfirst=True)
