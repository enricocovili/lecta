"""Maintenance commands (run inside the backend container).

  docker compose exec backend python -m app.cli reset-password     # prompts for a new password
  docker compose exec backend python -m app.cli disable-2fa
  docker compose exec backend python -m app.cli logout-all
  docker compose exec backend python -m app.cli setup-code         # print the one-time setup code
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import delete, select

from . import appsecrets
from .db import SessionLocal
from .models import AdminUser, Session
from .security.auth import hash_password


async def _admin(db) -> AdminUser:  # noqa: ANN001
    user = (await db.execute(select(AdminUser).limit(1))).scalar_one_or_none()
    if user is None:
        sys.exit("no admin account yet — open the site and run the setup wizard")
    return user


async def reset_password(password: str | None) -> None:
    if password is None:
        password = getpass.getpass("New password (≥ 12 characters): ")
        if password != getpass.getpass("Repeat: "):
            sys.exit("passwords don't match")
    if len(password) < 12:
        sys.exit("the password must be at least 12 characters")
    async with SessionLocal() as db:
        user = await _admin(db)
        user.password_hash = hash_password(password)
        await db.execute(delete(Session))
        await db.commit()
    print(f"password reset for {user.username}; all sessions were signed out")


async def disable_2fa() -> None:
    async with SessionLocal() as db:
        user = await _admin(db)
        user.totp_enabled = False
        user.totp_secret_enc = None
        await db.commit()
    print("two-factor authentication disabled")


async def logout_all() -> None:
    async with SessionLocal() as db:
        await db.execute(delete(Session))
        await db.commit()
    print("all sessions signed out")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("reset-password")
    rp.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    sub.add_parser("disable-2fa")
    sub.add_parser("logout-all")
    sub.add_parser("setup-code")
    args = p.parse_args()
    if args.cmd == "reset-password":
        asyncio.run(reset_password(sys.stdin.readline().rstrip("\n") if args.password_stdin else None))
    elif args.cmd == "disable-2fa":
        asyncio.run(disable_2fa())
    elif args.cmd == "logout-all":
        asyncio.run(logout_all())
    elif args.cmd == "setup-code":
        print(appsecrets.setup_code())


if __name__ == "__main__":
    main()
