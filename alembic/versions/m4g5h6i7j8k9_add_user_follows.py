"""add user_follows table and followers_count to users

Revision ID: m4g5h6i7j8k9
Revises: l3f4g5h6i7j8
Create Date: 2026-05-15 20:00:00.000000

"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "m4g5h6i7j8k9"
down_revision: Union[str, None] = "l3f4g5h6i7j8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "user_follows",
        sa.Column("follower_id", sa.Integer(), nullable=False),
        sa.Column("followed_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["follower_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["followed_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("follower_id", "followed_id"),
    )
    op.create_index(
        "ix_user_follows_followed_created",
        "user_follows",
        ["followed_id", "created_at"],
    )
    op.create_index(
        "ix_user_follows_follower_id",
        "user_follows",
        ["follower_id"],
    )

    op.add_column(
        "users",
        sa.Column(
            "followers_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "followers_count")
    op.drop_index("ix_user_follows_follower_id", table_name="user_follows")
    op.drop_index("ix_user_follows_followed_created", table_name="user_follows")
    op.drop_table("user_follows")
