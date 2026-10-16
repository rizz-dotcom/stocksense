"""Local authentication with scrypt password hashing (Python standard library).

* No plain-text passwords are stored.
* No default administrator account exists; the first admin is created
  explicitly (first-run screen or scripts/create_admin.py).
* Accounts lock for 15 minutes after 5 consecutive failed sign-ins.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from stocksense.database import Database
from stocksense.exceptions import AuthenticationError, DomainValidationError, NotFoundError
from stocksense.models import User
from stocksense.security.authorization import ROLES, Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.dates import utcnow

log = logging.getLogger("stocksense.auth")

_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 64
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
_USERNAME_RE = re.compile(r"^[a-z0-9_.\-]{3,50}$")
# Pre-computed hash used to keep timing similar when a username does not exist.
_DUMMY_HASH: str | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def validate_new_password(password: str, username: str) -> None:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("at least one letter and one digit")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    if password.lower() in {"password123", "admin12345", "stocksense1"}:
        problems.append("must not be a common password")
    if problems:
        raise DomainValidationError("Password requirements: " + "; ".join(problems) + ".")


def _normalize_username(username: str) -> str:
    name = (username or "").strip().lower()
    if not _USERNAME_RE.match(name):
        raise DomainValidationError(
            "Username must be 3-50 characters: lowercase letters, digits, '.', '_' or '-'.")
    return name


def create_first_admin(db: Database, username: str, password: str) -> UserContext:
    """Create the first administrator. Refused once any user exists."""
    name = _normalize_username(username)
    validate_new_password(password, name)
    with db.session() as s:
        if s.scalar(select(func.count(User.id))) > 0:
            raise DomainValidationError(
                "An administrator already exists. Sign in, or ask an admin to create your account.")
        user = User(username=name, password_hash=hash_password(password), role="admin")
        s.add(user)
        s.flush()
        audit_service.record(s, actor=name, action="USER_CREATED", entity_type="user",
                             entity_id=str(user.id), message="First administrator created.",
                             new_state="admin")
        return UserContext(user.id, user.username, user.role)


def create_user(db: Database, actor: UserContext, username: str, password: str,
                role: str) -> UserContext:
    require(actor, Permission.MANAGE_USERS)
    name = _normalize_username(username)
    if role not in ROLES:
        raise DomainValidationError(f"Unknown role '{role}'.")
    validate_new_password(password, name)
    with db.session() as s:
        if s.scalar(select(User.id).where(User.username == name)):
            raise DomainValidationError(f"Username '{name}' is already taken.")
        user = User(username=name, password_hash=hash_password(password), role=role)
        s.add(user)
        s.flush()
        audit_service.record(s, actor=actor.username, action="USER_CREATED", entity_type="user",
                             entity_id=str(user.id), message=f"User '{name}' created.",
                             new_state=role)
        return UserContext(user.id, user.username, user.role)


def authenticate(db: Database, username: str, password: str) -> UserContext:
    """Verify credentials. Failed attempts and lockouts are committed (and audited)
    BEFORE the error is raised, so a rollback can never erase them."""
    global _DUMMY_HASH
    name = (username or "").strip().lower()
    failure: AuthenticationError | None = None
    result: UserContext | None = None
    with db.session() as s:
        user = s.scalar(select(User).where(User.username == name))
        now = utcnow()
        if user is None or not user.is_active:
            if _DUMMY_HASH is None:
                _DUMMY_HASH = hash_password("dummy-password-0")
            verify_password(password or "", _DUMMY_HASH)
            audit_service.record(s, actor=None, action="LOGIN_FAILED", entity_type="user",
                                 entity_id=None, outcome="FAILURE",
                                 message="Sign-in failed: unknown or inactive account.")
            failure = AuthenticationError()
        elif user.locked_until and user.locked_until > now:
            audit_service.record(s, actor=None, action="LOGIN_BLOCKED", entity_type="user",
                                 entity_id=str(user.id), outcome="BLOCKED",
                                 message="Sign-in refused: account temporarily locked.")
            failure = AuthenticationError(
                "This account is temporarily locked after repeated failed sign-ins. "
                f"Try again in up to {LOCKOUT_MINUTES} minutes.")
        elif not verify_password(password or "", user.password_hash):
            user.failed_attempts += 1
            msg = "Sign-in failed: wrong password."
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_attempts = 0
                msg += " Account locked temporarily."
            audit_service.record(s, actor=None, action="LOGIN_FAILED", entity_type="user",
                                 entity_id=str(user.id), outcome="FAILURE", message=msg)
            failure = AuthenticationError()
        else:
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            audit_service.record(s, actor=user.username, action="LOGIN_SUCCEEDED",
                                 entity_type="user", entity_id=str(user.id), message="Signed in.")
            result = UserContext(user.id, user.username, user.role)
    if failure is not None:
        raise failure
    return result


def refresh_user(db: Database, user_id: int) -> UserContext:
    """Re-read the user so deactivation or role changes take effect immediately."""
    with db.session() as s:
        user = s.get(User, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Your account is no longer active.")
        return UserContext(user.id, user.username, user.role)


def list_users(db: Database, actor: UserContext) -> list[dict]:
    require(actor, Permission.MANAGE_USERS)
    with db.session() as s:
        return [{"id": u.id, "username": u.username, "role": u.role, "active": u.is_active,
                 "last_login": u.last_login_at, "created": u.created_at}
                for u in s.scalars(select(User).order_by(User.username))]


def set_user_active(db: Database, actor: UserContext, user_id: int, active: bool) -> None:
    require(actor, Permission.MANAGE_USERS)
    if user_id == actor.user_id and not active:
        raise DomainValidationError("You cannot deactivate your own account.")
    with db.session() as s:
        user = s.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if not active and user.role == "admin":
            admins = s.scalar(select(func.count(User.id)).where(
                User.role == "admin", User.is_active.is_(True)))
            if admins <= 1:
                raise DomainValidationError("The last active administrator cannot be deactivated.")
        before = "active" if user.is_active else "inactive"
        user.is_active = active
        audit_service.record(s, actor=actor.username, action="USER_STATUS_CHANGED",
                             entity_type="user", entity_id=str(user.id),
                             previous_state=before, new_state="active" if active else "inactive",
                             message=f"User '{user.username}' {'activated' if active else 'deactivated'}.")


def reset_password(db: Database, actor: UserContext, user_id: int, new_password: str) -> None:
    require(actor, Permission.MANAGE_USERS)
    with db.session() as s:
        user = s.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        validate_new_password(new_password, user.username)
        user.password_hash = hash_password(new_password)
        user.failed_attempts = 0
        user.locked_until = None
        audit_service.record(s, actor=actor.username, action="PASSWORD_RESET",
                             entity_type="user", entity_id=str(user.id),
                             message=f"Password reset for '{user.username}'.")


def change_own_password(db: Database, actor: UserContext, current: str, new_password: str) -> None:
    with db.session() as s:
        user = s.get(User, actor.user_id)
        if user is None or not verify_password(current, user.password_hash):
            raise AuthenticationError("Your current password is incorrect.")
        validate_new_password(new_password, user.username)
        user.password_hash = hash_password(new_password)
        audit_service.record(s, actor=actor.username, action="PASSWORD_CHANGED",
                             entity_type="user", entity_id=str(user.id),
                             message="User changed their own password.")
