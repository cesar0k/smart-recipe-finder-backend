"""Admin management script.

Usage:
    Create admin:
        docker compose exec app python scripts/create_admin.py create \
            --email admin@example.com \
            --username admin \
            --password 'secure_password'

    Transfer admin to another user:
        docker compose exec app python scripts/create_admin.py transfer \
            --to username_or_email
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.future import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services.user_service import get_user_by_email, get_user_by_username


async def create_admin(email: str, username: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        # Check if an admin already exists
        result = await db.execute(select(User).where(User.role == "admin"))
        existing_admin = result.scalar_one_or_none()
        if existing_admin:
            print(
                f"Error: admin already exists — {existing_admin.username} "
                f"({existing_admin.email}). Use 'transfer' to change admin."
            )
            sys.exit(1)

        existing = await get_user_by_email(db, email=email)
        if existing:
            print(f"Error: user with email '{email}' already exists.")
            sys.exit(1)

        existing = await get_user_by_username(db, username=username)
        if existing:
            print(f"Error: user with username '{username}' already exists.")
            sys.exit(1)

        user = User(
            email=email,
            username=username,
            hashed_password=hash_password(password),
            role="admin",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        print(f"Admin created: id={user.id}, username={user.username}, email={user.email}")


async def transfer_admin(target: str) -> None:
    async with AsyncSessionLocal() as db:
        # Find current admin
        result = await db.execute(select(User).where(User.role == "admin"))
        current_admin = result.scalar_one_or_none()
        if current_admin is None:
            print("Error: no admin exists. Use 'create' first.")
            sys.exit(1)

        # Find target user by username or email
        target_user = await get_user_by_username(db, username=target)
        if target_user is None:
            target_user = await get_user_by_email(db, email=target)
        if target_user is None:
            print(f"Error: user '{target}' not found.")
            sys.exit(1)

        if target_user.id == current_admin.id:
            print("Error: target user is already the admin.")
            sys.exit(1)

        if not target_user.is_active:
            print(f"Error: user '{target}' is deactivated.")
            sys.exit(1)

        # Transfer: old admin → user, target → admin
        old_username = current_admin.username
        current_admin.role = "user"
        target_user.role = "admin"
        db.add(current_admin)
        db.add(target_user)
        await db.commit()

        print(
            f"Admin transferred: {old_username} → user, "
            f"{target_user.username} → admin"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Admin management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    create_parser = subparsers.add_parser("create", help="Create a new admin user")
    create_parser.add_argument("--email", required=True, help="Admin email")
    create_parser.add_argument("--username", required=True, help="Admin username")
    create_parser.add_argument("--password", required=True, help="Admin password")

    # transfer
    transfer_parser = subparsers.add_parser("transfer", help="Transfer admin role to another user")
    transfer_parser.add_argument("--to", required=True, dest="target", help="Username or email of the new admin")

    args = parser.parse_args()

    if args.command == "create":
        if len(args.password) < 8:
            print("Error: password must be at least 8 characters.")
            sys.exit(1)
        asyncio.run(create_admin(args.email, args.username, args.password))

    elif args.command == "transfer":
        asyncio.run(transfer_admin(args.target))


if __name__ == "__main__":
    main()
