"""Role-based access control. Checked in the service layer, not only in the UI."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from stocksense.exceptions import PermissionDeniedError


class Permission(str, Enum):
    VIEW = "view"
    MANAGE_INVENTORY = "manage_inventory"
    MANAGE_SUPPLIERS = "manage_suppliers"
    MANAGE_SALES = "manage_sales"
    RUN_FORECAST = "run_forecast"
    MANAGE_PROPOSALS = "manage_proposals"
    APPROVE_PROPOSALS = "approve_proposals"
    RECEIVE_ORDERS = "receive_orders"
    REQUEST_AI_REVIEW = "request_ai_review"
    VIEW_AUDIT = "view_audit"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_USERS = "manage_users"


ROLES = ("admin", "manager", "staff", "viewer")

_STAFF = {Permission.VIEW, Permission.MANAGE_INVENTORY, Permission.MANAGE_SUPPLIERS,
          Permission.MANAGE_SALES, Permission.RUN_FORECAST, Permission.MANAGE_PROPOSALS,
          Permission.RECEIVE_ORDERS, Permission.REQUEST_AI_REVIEW}

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "admin": frozenset(Permission),
    "manager": frozenset(_STAFF | {Permission.APPROVE_PROPOSALS, Permission.VIEW_AUDIT}),
    "staff": frozenset(_STAFF),
    "viewer": frozenset({Permission.VIEW}),
}

ROLE_DESCRIPTIONS = {
    "admin": "Everything, including users and settings",
    "manager": "Operations plus approving purchase proposals and viewing the audit log",
    "staff": "Inventory, suppliers, sales, forecasts and drafting proposals (cannot approve)",
    "viewer": "Read-only access",
}


@dataclass(frozen=True)
class UserContext:
    """The authenticated actor passed into every service call."""
    user_id: int
    username: str
    role: str

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, frozenset())


def require(user: UserContext | None, permission: Permission) -> UserContext:
    if user is None:
        raise PermissionDeniedError("You must be signed in.")
    if not user.can(permission):
        raise PermissionDeniedError(
            f"Your role ({user.role}) does not allow this action ({permission.value}).")
    return user
