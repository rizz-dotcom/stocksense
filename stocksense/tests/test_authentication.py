import pytest

from stocksense.exceptions import (AuthenticationError, DomainValidationError,
                                   PermissionDeniedError)
from stocksense.security import authentication as auth
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from tests.conftest import PASSWORD


def test_password_is_hashed_not_stored_plain(db):
    auth.create_first_admin(db, "Owner", "Str0ng-password")
    from stocksense.models import User
    with db.session() as s:
        stored = s.query(User).one().password_hash
    assert "Str0ng-password" not in stored and stored.startswith("scrypt$")
    assert auth.verify_password("Str0ng-password", stored)
    assert not auth.verify_password("wrong", stored)


def test_first_admin_only_once(db):
    auth.create_first_admin(db, "owner", "Str0ng-password")
    with pytest.raises(DomainValidationError):
        auth.create_first_admin(db, "intruder", "Str0ng-password2")


@pytest.mark.parametrize("pw", ["short1", "allletterslong", "1234567890", "owner12345678"])
def test_weak_passwords_rejected(db, pw):
    with pytest.raises(DomainValidationError):
        auth.create_first_admin(db, "owner", pw)


def test_login_success_and_failure_are_audited(db, admin):
    user = auth.authenticate(db, "ADMIN", PASSWORD)
    assert user.role == "admin"
    with pytest.raises(AuthenticationError):
        auth.authenticate(db, "admin", "wrong")
    with pytest.raises(AuthenticationError):
        auth.authenticate(db, "ghost", "whatever")
    actions = [e["action"] for e in audit_service.list_events(db, entity_type="user")]
    assert "LOGIN_SUCCEEDED" in actions and actions.count("LOGIN_FAILED") == 2


def test_lockout_after_repeated_failures(db, admin):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            auth.authenticate(db, "admin", "wrong")
    with pytest.raises(AuthenticationError) as exc:
        auth.authenticate(db, "admin", PASSWORD)
    assert "locked" in exc.value.user_message


def test_deactivated_user_cannot_sign_in_or_refresh(db, admin, staff):
    auth.set_user_active(db, admin, staff.user_id, False)
    with pytest.raises(AuthenticationError):
        auth.authenticate(db, "staff", PASSWORD)
    with pytest.raises(AuthenticationError):
        auth.refresh_user(db, staff.user_id)


def test_last_admin_cannot_be_deactivated(db, admin, manager):
    with pytest.raises(DomainValidationError):
        auth.set_user_active(db, admin, admin.user_id, False)


def test_user_management_requires_admin(db, manager):
    with pytest.raises(PermissionDeniedError):
        auth.create_user(db, manager, "newbie", "Str0ng-password", "staff")
    with pytest.raises(PermissionDeniedError):
        auth.list_users(db, manager)


def test_role_permissions():
    assert UserContext(1, "m", "manager").can(Permission.APPROVE_PROPOSALS)
    assert not UserContext(1, "s", "staff").can(Permission.APPROVE_PROPOSALS)
    assert not UserContext(1, "v", "viewer").can(Permission.MANAGE_INVENTORY)
    assert not UserContext(1, "x", "hacker").can(Permission.VIEW)
    with pytest.raises(PermissionDeniedError):
        require(None, Permission.VIEW)


def test_unauthorized_record_access(db, staff, viewer):
    from stocksense.services import settings_service
    with pytest.raises(PermissionDeniedError):
        settings_service.update_settings(db, staff, {"business_name": "x"})
    with pytest.raises(PermissionDeniedError):
        auth.reset_password(db, viewer, staff.user_id, "N3w-password-x")


def test_change_own_password(db, staff):
    auth.change_own_password(db, staff, PASSWORD, "Brand-n3w-pass")
    assert auth.authenticate(db, "staff", "Brand-n3w-pass").username == "staff"
    with pytest.raises(AuthenticationError):
        auth.change_own_password(db, staff, "wrong", "Another-n3w-pass")
