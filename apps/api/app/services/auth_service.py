from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import User, UserRole

ALGORITHM = "HS256"

# Local demo logins. Seeded by `dclab user seed`. Passwords are for the
# development database only — never use these outside this machine.
DEMO_ADMIN_EMAIL = "admin@dclab.io"
DEMO_ADMIN_PASSWORD = "AdminPass123"
DEMO_ADMIN_NAME = "Admin"
DEMO_CLIENT_EMAIL = "demo@client.io"
DEMO_CLIENT_PASSWORD = "ClientPass123"
DEMO_CLIENT_NAME = "Business Client"


class AuthError(Exception):
    """Raised when a credential or token is missing, malformed, or expired."""


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash in the row — treat as a failed login, never a 500.
        return False


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "full_name": user.full_name or user.email,
        "workspace_id": str(user.workspace_id) if user.workspace_id else None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email.strip().lower()).one_or_none()
    # Hash even when the user is missing so a wrong email and a wrong password take
    # the same time — otherwise response timing enumerates valid accounts.
    if user is None:
        verify_password(password, hash_password("no-such-user"))
        raise AuthError("invalid email or password")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")
    if not user.is_active:
        raise AuthError("account is disabled")
    return user


def user_from_token(db: Session, token: str) -> User:
    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not subject:
        raise AuthError("token is missing a subject")
    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise AuthError("token subject is not a user id") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("user no longer exists or is disabled")
    return user


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    role: UserRole,
    full_name: str = "",
    workspace_id: uuid.UUID | None = None,
) -> User:
    if role is UserRole.CLIENT_USER and workspace_id is None:
        raise ValueError("client_user requires a workspace_id")
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=role.value,
        full_name=full_name,
        workspace_id=workspace_id if role is UserRole.CLIENT_USER else None,
    )
    db.add(user)
    db.flush()
    return user


def ensure_demo_users(db: Session) -> list[User]:
    """Create or refresh the two local logins: one staff, one customer."""
    from app.db.models import DEFAULT_WORKSPACE_ID

    specs = (
        {
            "email": DEMO_ADMIN_EMAIL,
            "password": DEMO_ADMIN_PASSWORD,
            "role": UserRole.DCLAB_ADMIN,
            "full_name": DEMO_ADMIN_NAME,
            "workspace_id": None,
        },
        {
            "email": DEMO_CLIENT_EMAIL,
            "password": DEMO_CLIENT_PASSWORD,
            "role": UserRole.CLIENT_USER,
            "full_name": DEMO_CLIENT_NAME,
            "workspace_id": DEFAULT_WORKSPACE_ID,
        },
    )
    users: list[User] = []
    for spec in specs:
        email = spec["email"].strip().lower()
        existing = db.query(User).filter(User.email == email).one_or_none()
        if existing is None:
            users.append(
                create_user(
                    db,
                    email=email,
                    password=spec["password"],
                    role=spec["role"],
                    full_name=spec["full_name"],
                    workspace_id=spec["workspace_id"],
                )
            )
            continue
        existing.password_hash = hash_password(spec["password"])
        existing.role = spec["role"].value
        existing.full_name = spec["full_name"]
        existing.workspace_id = spec["workspace_id"]
        existing.is_active = True
        users.append(existing)
    db.flush()
    return users
