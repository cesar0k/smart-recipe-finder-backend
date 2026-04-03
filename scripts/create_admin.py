"""Create an admin user.

Usage:
    docker compose exec app python scripts/create_admin.py \
        --email admin@example.com \
        --username admin \
        --password 'secure_password'
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services.user_service import get_user_by_email, get_user_by_username


async def create_admin(email: str, username: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
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

        print(f"Admin user created: id={user.id}, email={user.email}, username={user.username}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an admin user")
    parser.add_argument("--email", required=True, help="Admin email")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Admin password")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters.")
        sys.exit(1)

    asyncio.run(create_admin(args.email, args.username, args.password))


if __name__ == "__main__":
    main()
