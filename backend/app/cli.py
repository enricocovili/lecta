"""Maintenance commands (run inside the backend container).

  docker compose exec backend python -m app.cli reset-password     # prompts for a new password
  docker compose exec backend python -m app.cli disable-2fa
  docker compose exec backend python -m app.cli logout-all
  docker compose exec backend python -m app.cli setup-code         # print the one-time setup code

`reset-password`, `disable-2fa` and `logout-all` take `--user NAME`; without it they act on the only admin
(`logout-all` without it signs out everybody).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import delete, select

from . import appsecrets
from .db import SessionLocal
from .models import Session, User
from .security.auth import hash_password


async def _user(db, username: str | None) -> User:  # noqa: ANN001
    if username is not None:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            sys.exit(f"no user named {username!r}")
        return user
    admins = (await db.execute(select(User).where(User.role == "admin").order_by(User.id))).scalars().all()
    if not admins:
        sys.exit("no admin account yet — open the site and run the setup wizard")
    if len(admins) > 1:
        sys.exit("there are several admins (" + ", ".join(a.username for a in admins) + "): say which one with --user")
    return admins[0]


async def reset_password(password: str | None, username: str | None = None) -> None:
    if password is None:
        password = getpass.getpass("New password (≥ 12 characters): ")
        if password != getpass.getpass("Repeat: "):
            sys.exit("passwords don't match")
    if len(password) < 12:
        sys.exit("the password must be at least 12 characters")
    async with SessionLocal() as db:
        user = await _user(db, username)
        user.password_hash = hash_password(password)
        await db.execute(delete(Session).where(Session.user_id == user.id))
        await db.commit()
    print(f"password reset for {user.username}; their sessions were signed out")


async def disable_2fa(username: str | None = None) -> None:
    async with SessionLocal() as db:
        user = await _user(db, username)
        user.totp_enabled = False
        user.totp_secret_enc = None
        await db.commit()
    print(f"two-factor authentication disabled for {user.username}")


async def logout_all(username: str | None = None) -> None:
    async with SessionLocal() as db:
        if username is None:
            await db.execute(delete(Session))
        else:
            user = await _user(db, username)
            await db.execute(delete(Session).where(Session.user_id == user.id))
        await db.commit()
    print("all sessions signed out" if username is None else f"all sessions of {username} signed out")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("reset-password")
    rp.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    rp.add_argument("--user", help="the account (default: the only admin)")
    sub.add_parser("disable-2fa").add_argument("--user", help="the account (default: the only admin)")
    sub.add_parser("logout-all").add_argument("--user", help="only this account's sessions (default: everybody's)")
    sub.add_parser("setup-code")
    args = p.parse_args()
    if args.cmd == "reset-password":
        asyncio.run(reset_password(sys.stdin.readline().rstrip("\n") if args.password_stdin else None, args.user))
    elif args.cmd == "disable-2fa":
        asyncio.run(disable_2fa(args.user))
    elif args.cmd == "logout-all":
        asyncio.run(logout_all(args.user))
    elif args.cmd == "setup-code":
        print(appsecrets.setup_code())


if __name__ == "__main__":
    main()
