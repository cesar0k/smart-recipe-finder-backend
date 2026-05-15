"""add smtp email verification and notification preferences

Revision ID: k2e3f4g5h6i7
Revises: j1d2e3f4g5h6
Create Date: 2026-05-15 12:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "k2e3f4g5h6i7"
down_revision: Union[str, None] = "j1d2e3f4g5h6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # ── users: email verification + password reset token fields ──────────────
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_token", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_sent_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_token", sa.String(128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_expires_at", sa.DateTime(), nullable=True),
    )

    # ── email_notification_preferences ───────────────────────────────────────
    op.create_table(
        "email_notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "type", name="uq_email_prefs_user_type"),
    )
    op.create_index(
        "ix_email_prefs_user_id",
        "email_notification_preferences",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_prefs_user_id", table_name="email_notification_preferences")
    op.drop_table("email_notification_preferences")

    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token")
    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verification_token")
    op.drop_column("users", "email_verified")
