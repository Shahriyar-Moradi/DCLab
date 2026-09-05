from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    PlatformMembership,
    PlatformRole,
    User,
    UserRole,
    WorkspaceMembership,
    WorkspaceRole,
)

ALGORITHM = "HS256"

# Local demo logins. Seeded by `dclab user seed`. Passwords are for the
# development database only — never use these outside this machine.
DEMO_ADMIN_EMAIL = "admin@dclab.io"
DEMO_ADMIN_PASSWORD = "AdminPass123"
DEMO_ADMIN_NAME = "DCLab Admin"
DEMO_DEVELOPER_EMAIL = "developer@dclab.io"
DEMO_DEVELOPER_PASSWORD = "DeveloperPass123"
DEMO_DEVELOPER_NAME = "DCLab Developer"
DEMO_CLIENT_EMAIL = "demo@client.io"
DEMO_CLIENT_PASSWORD = "ClientPass123"
DEMO_CLIENT_NAME = "Business Client"
DEMO_BUSINESS_ADMIN_EMAIL = "business-admin@dclab.io"
DEMO_BUSINESS_ADMIN_PASSWORD = "BusinessAdminPass123"
DEMO_BUSINESS_ADMIN_NAME = "Business Admin"
DEMO_BUSINESS_DEVELOPER_EMAIL = "business-developer@dclab.io"
DEMO_BUSINESS_DEVELOPER_PASSWORD = "BusinessDevPass123"
DEMO_BUSINESS_DEVELOPER_NAME = "Business Developer"
DEMO_PERSONAL_EMAIL = "personal@dclab.io"
DEMO_PERSONAL_PASSWORD = "PersonalPass123"
DEMO_PERSONAL_NAME = "Personal Developer"
DEMO_PERSONAL_WORKSPACE_SLUG = "demo-personal"
DEMO_PERSONAL_WORKSPACE_NAME = "Personal Lab"

# home: platform | default | personal
DEMO_ACCOUNTS: tuple[dict[str, object], ...] = (
    {
        "email": DEMO_ADMIN_EMAIL,
        "password": DEMO_ADMIN_PASSWORD,
        "role": UserRole.DCLAB_ADMIN,
        "full_name": DEMO_ADMIN_NAME,
        "home": "platform",
    },
    {
        "email": DEMO_DEVELOPER_EMAIL,
        "password": DEMO_DEVELOPER_PASSWORD,
        "role": UserRole.DCLAB_DEVELOPER,
        "full_name": DEMO_DEVELOPER_NAME,
        "home": "platform",
    },
    {
        "email": DEMO_CLIENT_EMAIL,
        "password": DEMO_CLIENT_PASSWORD,
        "role": UserRole.CLIENT_USER,
        "full_name": DEMO_CLIENT_NAME,
        "home": "default",
    },
    {
        "email": DEMO_BUSINESS_ADMIN_EMAIL,
        "password": DEMO_BUSINESS_ADMIN_PASSWORD,
        "role": UserRole.BUSINESS_ADMIN,
        "full_name": DEMO_BUSINESS_ADMIN_NAME,
        "home": "default",
    },
    {
        "email": DEMO_BUSINESS_DEVELOPER_EMAIL,
        "password": DEMO_BUSINESS_DEVELOPER_PASSWORD,
        "role": UserRole.BUSINESS_DEVELOPER,
        "full_name": DEMO_BUSINESS_DEVELOPER_NAME,
        "home": "default",
    },
    {
        "email": DEMO_PERSONAL_EMAIL,
        "password": DEMO_PERSONAL_PASSWORD,
        "role": UserRole.WORKSPACE_OWNER,
        "full_name": DEMO_PERSONAL_NAME,
        "home": "personal",
    },
)


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


def register_customer(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str = "",
) -> User:
    """Create a customer login with no workspace. They then create a personal or business workspace."""

    from app.domain.errors import IdentityError

    normalized = email.strip().lower()
    if len(password) < 8:
        raise IdentityError("password must be at least 8 characters", status_code=400)
    existing = db.query(User).filter(User.email == normalized).one_or_none()
    if existing is not None:
        raise IdentityError("email already registered", status_code=409)
    return create_user(
        db,
        email=normalized,
        password=password,
        role=UserRole.WORKSPACE_OWNER,
        full_name=full_name,
        workspace_id=None,
    )


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    role: UserRole,
    full_name: str = "",
    workspace_id: uuid.UUID | None = None,
) -> User:
    legacy_workspace_roles = {
        UserRole.CLIENT_USER: WorkspaceRole.BUSINESS_ADMIN,
        UserRole.BUSINESS_ADMIN: WorkspaceRole.BUSINESS_ADMIN,
        UserRole.BUSINESS_DEVELOPER: WorkspaceRole.BUSINESS_DEVELOPER,
    }
    canonical_workspace_roles = {
        UserRole.WORKSPACE_OWNER: WorkspaceRole.WORKSPACE_OWNER,
        UserRole.WORKSPACE_ADMIN: WorkspaceRole.WORKSPACE_ADMIN,
        UserRole.ML_ENGINEER: WorkspaceRole.ML_ENGINEER,
        UserRole.VIEWER: WorkspaceRole.VIEWER,
    }
    workspace_roles = {**legacy_workspace_roles, **canonical_workspace_roles}
    platform_roles = {
        UserRole.DCLAB_ADMIN: PlatformRole.DCLAB_ADMIN,
        UserRole.DCLAB_DEVELOPER: PlatformRole.DCLAB_DEVELOPER,
    }
    if role in legacy_workspace_roles and workspace_id is None:
        raise ValueError(f"{role.value} requires a workspace_id")
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        role=role.value,
        full_name=full_name,
        workspace_id=workspace_id if role in workspace_roles else None,
    )
    db.add(user)
    db.flush()
    if role in platform_roles:
        db.add(PlatformMembership(user_id=user.id, role=platform_roles[role].value))
    elif role in workspace_roles and workspace_id is not None:
        db.add(
            WorkspaceMembership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=workspace_roles[role].value,
            )
        )
    db.flush()
    return user


def demo_logins() -> list[dict[str, str]]:
    """Plain-text local logins printed by `dclab user seed` and shown on /login."""
    return [
        {
            "email": str(spec["email"]),
            "password": str(spec["password"]),
            "role": spec["role"].value if isinstance(spec["role"], UserRole) else str(spec["role"]),
            "name": str(spec["full_name"]),
        }
        for spec in DEMO_ACCOUNTS
    ]


def _platform_role_for(role: UserRole) -> PlatformRole | None:
    mapping = {
        UserRole.DCLAB_ADMIN: PlatformRole.DCLAB_ADMIN,
        UserRole.DCLAB_DEVELOPER: PlatformRole.DCLAB_DEVELOPER,
    }
    return mapping.get(role)


def _workspace_role_for(role: UserRole) -> WorkspaceRole | None:
    mapping = {
        UserRole.CLIENT_USER: WorkspaceRole.BUSINESS_ADMIN,
        UserRole.BUSINESS_ADMIN: WorkspaceRole.BUSINESS_ADMIN,
        UserRole.BUSINESS_DEVELOPER: WorkspaceRole.BUSINESS_DEVELOPER,
        UserRole.WORKSPACE_OWNER: WorkspaceRole.WORKSPACE_OWNER,
        UserRole.WORKSPACE_ADMIN: WorkspaceRole.WORKSPACE_ADMIN,
        UserRole.ML_ENGINEER: WorkspaceRole.ML_ENGINEER,
        UserRole.VIEWER: WorkspaceRole.VIEWER,
    }
    return mapping.get(role)


def _sync_platform_membership(db: Session, user: User, role: UserRole) -> None:
    wanted = _platform_role_for(role)
    membership = db.query(PlatformMembership).filter_by(user_id=user.id).one_or_none()
    if wanted is None:
        if membership is not None:
            db.delete(membership)
        return
    if membership is None:
        db.add(PlatformMembership(user_id=user.id, role=wanted.value))
        return
    membership.role = wanted.value


def _sync_workspace_membership(
    db: Session,
    user: User,
    role: UserRole,
    workspace_id: uuid.UUID | None,
) -> None:
    wanted = _workspace_role_for(role)
    if wanted is None or workspace_id is None:
        return
    membership = (
        db.query(WorkspaceMembership)
        .filter_by(user_id=user.id, workspace_id=workspace_id)
        .one_or_none()
    )
    if membership is None:
        db.add(
            WorkspaceMembership(
                user_id=user.id,
                workspace_id=workspace_id,
                role=wanted.value,
            )
        )
        return
    membership.role = wanted.value


def _ensure_personal_workspace(db: Session, owner: User) -> uuid.UUID:
    from app.db.models import Workspace
    from app.services.workspace_service import create_personal_workspace

    workspace = (
        db.query(Workspace)
        .filter_by(slug=DEMO_PERSONAL_WORKSPACE_SLUG)
        .one_or_none()
    )
    if workspace is None:
        created = create_personal_workspace(
            db,
            owner=owner,
            name=DEMO_PERSONAL_WORKSPACE_NAME,
            slug=DEMO_PERSONAL_WORKSPACE_SLUG,
        )
        return created.id
    owner.workspace_id = workspace.id
    _sync_workspace_membership(db, owner, UserRole.WORKSPACE_OWNER, workspace.id)
    db.flush()
    return workspace.id


def ensure_demo_users(db: Session) -> list[User]:
    """Create or refresh local logins for every product role used in development."""
    from app.db.models import DEFAULT_WORKSPACE_ID

    users: list[User] = []
    for spec in DEMO_ACCOUNTS:
        email = str(spec["email"]).strip().lower()
        raw_role = spec["role"]
        role = raw_role if isinstance(raw_role, UserRole) else UserRole(str(raw_role))
        home = str(spec["home"])
        workspace_id = DEFAULT_WORKSPACE_ID if home == "default" else None
        existing = db.query(User).filter(User.email == email).one_or_none()
        if existing is None:
            user = create_user(
                db,
                email=email,
                password=str(spec["password"]),
                role=role,
                full_name=str(spec["full_name"]),
                workspace_id=workspace_id,
            )
        else:
            user = existing
            user.password_hash = hash_password(str(spec["password"]))
            user.role = role.value
            user.full_name = str(spec["full_name"])
            user.workspace_id = workspace_id
            user.is_active = True
            _sync_platform_membership(db, user, role)
            _sync_workspace_membership(db, user, role, workspace_id)
        if home == "personal":
            user.role = UserRole.WORKSPACE_OWNER.value
            personal_id = _ensure_personal_workspace(db, user)
            user.workspace_id = personal_id
        users.append(user)
    db.flush()
    return users
