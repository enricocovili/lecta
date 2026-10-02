"""Authentication: argon2 passwords, DB-backed sessions, CSRF, rate limits, TOTP.

Access rule: every private endpoint depends on `require_admin`, which raises
404 (not 401) for anonymous callers, so private resources are indistinguishable
from unknown ones. Unsafe methods additionally require the session's CSRF token
in the `X-CSRF-Token` header.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..db import get_db
from ..models import AdminUser, Session

SESSION_COOKIE = "lecta_session"
CSRF_COOKIE = "lecta_csrf"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL = timedelta(days=30)
SESSION_IDLE = timedelta(days=7)
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

_hasher = PasswordHasher()  # argon2id with library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hash_: str) -> bool:
    return _hasher.check_needs_rehash(hash_)


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def is_https(request: Request) -> bool:
    if config.cookie_secure == "always":
        return True
    if config.cookie_secure == "never":
        return False
    return request.scope.get("scheme") == "https"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- rate limit


class RateLimiter:
    """Sliding-window limiter kept in memory (single backend process)."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window = window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self.hits.pop(key, None)


login_limiter = RateLimiter(limit=10, window_s=15 * 60)
# Global cap across all IPs so a distributed guesser is slowed down too.
login_global_limiter = RateLimiter(limit=100, window_s=15 * 60)


def check_login_rate(request: Request) -> None:
    ip = client_ip(request)
    if login_limiter.blocked(ip) or login_global_limiter.blocked("*"):
        raise HTTPException(status_code=429, detail="Troppi tentativi di accesso, riprova più tardi")


def record_login_failure(request: Request) -> None:
    login_limiter.hit(client_ip(request))
    login_global_limiter.hit("*")


# --------------------------------------------------------------------------- CSRF


def ensure_csrf_cookie(request: Request, response: Response) -> str:
    """Pre-session CSRF token (double-submit) for login/setup forms."""
    token = request.cookies.get(CSRF_COOKIE)
    if not token or len(token) < 32:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            CSRF_COOKIE, token, httponly=False, samesite="strict", secure=is_https(request), path="/"
        )
    return token


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients; the token check still applies
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return origin.split("://", 1)[-1].rstrip("/") == host


def check_double_submit(request: Request) -> None:
    """For unauthenticated unsafe endpoints (login, setup)."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not hmac.compare_digest(cookie, header) or not _same_origin(request):
        raise HTTPException(status_code=403, detail="CSRF check failed")


# --------------------------------------------------------------------------- sessions


@dataclass
class AuthContext:
    user: AdminUser
    session: Session


async def create_session(db: AsyncSession, request: Request, response: Response, user: AdminUser) -> Session:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    sess = Session(
        id=_token_id(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(sess)
    await db.commit()
    secure = is_https(request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    set_session_csrf_cookie(request, response, sess)
    return sess


def set_session_csrf_cookie(request: Request, response: Response, sess: Session) -> None:
    # Same lifetime as the session cookie: if the browser dropped it while keeping the
    # session, every unsafe request would fail the CSRF check.
    response.set_cookie(
        CSRF_COOKIE, sess.csrf_token, httponly=False, samesite="strict", secure=is_https(request), path="/",
        max_age=int(SESSION_TTL.total_seconds()),
    )


async def destroy_session(db: AsyncSession, response: Response, sess: Session | None) -> None:
    if sess is not None:
        await db.execute(delete(Session).where(Session.id == sess.id))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


async def load_session(request: Request, db: AsyncSession) -> AuthContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = await db.get(Session, _token_id(token))
    if sess is None:
        return None
    now = datetime.now(UTC)
    if sess.expires_at <= now or now - sess.last_seen_at > SESSION_IDLE:
        await db.delete(sess)
        await db.commit()
        return None
    user = await db.get(AdminUser, sess.user_id)
    if user is None:
        return None
    if now - sess.last_seen_at > timedelta(minutes=5):
        sess.last_seen_at = now
        await db.commit()
    return AuthContext(user=user, session=sess)


async def optional_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext | None:
    return await load_session(request, db)


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    ctx = getattr(request.state, "auth", None) or await load_session(request, db)
    if ctx is None:
        raise not_found()
    if request.method in UNSAFE:
        header = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(header, ctx.session.csrf_token) or not _same_origin(request):
            raise HTTPException(status_code=403, detail="CSRF check failed")
    request.state.auth = ctx
    return ctx


async def get_admin_user(db: AsyncSession) -> AdminUser | None:
    return (await db.execute(select(AdminUser).limit(1))).scalar_one_or_none()


# --------------------------------------------------------------------------- TOTP


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_uri(secret: str, username: str, issuer: str = "Lecta") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)
