from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from .base import Base
from .enums import NotificationType, pg_enum


class EmailNotificationPreference(Base):
    __tablename__ = "email_notification_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[NotificationType] = mapped_column(
        pg_enum(NotificationType, name="notification_type"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "type", name="uq_email_prefs_user_type"),
        Index("ix_email_prefs_user_id", "user_id"),
    )
