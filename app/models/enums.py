"""Python str-Enum classes mirrored 1:1 with Postgres ENUM types.

Why str-Enum: inheriting from `str` means a comparison like
`recipe.status == "approved"` keeps working, and JSON serialization of the
value via Pydantic uses the string value directly. SQLAlchemy reads/writes
the column as the enum member when the column is declared with
``pg_enum(RecipeStatus, name="recipe_status")``.

The `pg_enum()` helper below is the canonical way to wire a column — it sets
`values_callable` so SQLAlchemy sends the *value* (e.g. "approved") rather
than the *name* (e.g. "APPROVED") to Postgres, which is what the database
ENUM type actually accepts.

`create_type=False` on the column because the ENUM types are created by the
Alembic migration, not at runtime — keeps test/dev/prod schemas in sync.
"""

from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=Enum)


def pg_enum(enum_cls: type[E], *, name: str) -> SAEnum:
    """Standardised SQLAlchemy Enum column for a Postgres native ENUM type.

    - `values_callable` makes the ORM send the enum *value* (lowercase string
      we defined) to the database, not the member name (uppercase).
    - `native_enum=True` selects the PG ENUM backend, not a CHECK constraint.
    - `create_type=False` because Alembic owns the type lifecycle.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )


# Backwards-friendly alias for callers that import the raw class. New code
# should use pg_enum().
def _values_callable(obj: Any) -> list[str]:
    return [e.value for e in obj]


class UserRole(str, Enum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class AuthProvider(str, Enum):
    LOCAL = "local"
    GOOGLE = "google"


class UserLanguage(str, Enum):
    RU = "ru"
    EN = "en"


class RecipeDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class RecipeStatus(str, Enum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class DraftStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class NotificationType(str, Enum):
    NEW_COMMENT = "new_comment"
    COMMENT_REPLY = "comment_reply"
    COMMENT_REPORTED = "comment_reported"
    NEW_PENDING_RECIPE = "new_pending_recipe"
    RECIPE_APPROVED = "recipe_approved"
    RECIPE_REJECTED = "recipe_rejected"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_REJECTED = "draft_rejected"
    RECIPE_DELETED = "recipe_deleted"
    USER_FOLLOWED = "user_followed"
    FOLLOWED_USER_PUBLISHED = "followed_user_published"


# ── RecipeTags enums ──────────────────────────────────────────────────────
# Values come from the LLM tag-classifier prompt (see tag_service.py) and
# what's actually present in the seeded data (verified via SELECT DISTINCT).

class MealType(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    DESSERT = "dessert"
    SNACK = "snack"
    DRINK = "drink"
    SOUP = "soup"
    SALAD = "salad"
    SIDE = "side"
    OTHER = "other"


class MainProtein(str, Enum):
    BEEF = "beef"
    PORK = "pork"
    CHICKEN = "chicken"
    FISH = "fish"
    SEAFOOD = "seafood"
    EGGS = "eggs"
    LEGUMES = "legumes"
    NONE = "none"


class CookingMethod(str, Enum):
    BAKED = "baked"
    FRIED = "fried"
    BOILED = "boiled"
    STEWED = "stewed"
    ROASTED = "roasted"
    RAW = "raw"
    NO_COOK = "no_cook"
    SLOW_COOKED = "slow_cooked"
    OTHER = "other"


class SpiceLevel(str, Enum):
    NONE = "none"
    MILD = "mild"
    MEDIUM = "medium"
    HOT = "hot"


class Occasion(str, Enum):
    EVERYDAY = "everyday"
    HOLIDAY = "holiday"
    PARTY = "party"
    BRUNCH = "brunch"
    PICNIC = "picnic"
    BARBECUE = "barbecue"
    KIDS_FRIENDLY = "kids_friendly"


class CostTier(str, Enum):
    BUDGET = "budget"
    MODERATE = "moderate"
    PREMIUM = "premium"


class TechniqueDifficulty(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
