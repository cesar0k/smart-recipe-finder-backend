from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String  # noqa: F401 Integer reused below
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.models._base.base import Base
from app.models._base.enums import AuthProvider, UserLanguage, UserRole, pg_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        pg_enum(AuthProvider, name="auth_provider"),
        default=AuthProvider.LOCAL,
        server_default=AuthProvider.LOCAL.value,
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, name="user_role"),
        default=UserRole.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    # Email verification
    email_verified: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False, default=False
    )
    email_verification_token: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Password reset
    password_reset_token: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Pending email change (new email waits for confirmation before replacing current)
    pending_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pending_email_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Follower count (denormalised, recomputed on follow/unfollow)
    followers_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), nullable=False, default=0
    )

    # UI / email language preference: "ru" or "en"
    language: Mapped[UserLanguage] = mapped_column(
        pg_enum(UserLanguage, name="user_language"),
        server_default=text("'ru'"),
        nullable=False,
        default=UserLanguage.RU,
    )
