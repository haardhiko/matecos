"""
authorization.py
=================
Authorization — RBAC and permission checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class Permission(str, Enum):
    """System permissions."""

    SUBMIT_REQUEST = "submit_request"
    VIEW_STATUS = "view_status"
    CANCEL_EXECUTION = "cancel_execution"
    APPROVE_ACTION = "approve_action"
    REJECT_ACTION = "reject_action"
    VIEW_TOOLS = "view_tools"
    REGISTER_TOOL = "register_tool"
    VIEW_MEMORY = "view_memory"
    ADMIN = "admin"


@dataclass(frozen=True)
class Role:
    """A named set of permissions."""

    name: str
    permissions: frozenset[Permission]


# Pre-defined roles
VIEWER_ROLE = Role(
    name="viewer",
    permissions=frozenset({Permission.VIEW_STATUS, Permission.VIEW_TOOLS}),
)
USER_ROLE = Role(
    name="user",
    permissions=frozenset(
        {
            Permission.SUBMIT_REQUEST,
            Permission.VIEW_STATUS,
            Permission.CANCEL_EXECUTION,
            Permission.APPROVE_ACTION,
            Permission.REJECT_ACTION,
            Permission.VIEW_TOOLS,
            Permission.VIEW_MEMORY,
        }
    ),
)
ADMIN_ROLE = Role(
    name="admin",
    permissions=frozenset(Permission),
)

ROLE_MAP: dict[str, Role] = {
    "viewer": VIEWER_ROLE,
    "user": USER_ROLE,
    "admin": ADMIN_ROLE,
}


class AuthorizationService:
    """Checks whether a user has permission to perform an action."""

    def __init__(self) -> None:
        self._log = logger.bind(component="AuthorizationService")

    def check(
        self,
        user: dict[str, Any],
        permission: Permission,
    ) -> bool:
        """Check if a user has a specific permission.

        Args:
            user: The user dict (from auth dependency).
            permission: The required permission.

        Returns:
            ``True`` if the user has the permission.
        """
        scopes: list[str] = user.get("scopes", [])
        user_permissions: set[Permission] = set()

        for scope in scopes:
            role = ROLE_MAP.get(scope)
            if role:
                user_permissions.update(role.permissions)
            # Direct permission grant
            try:
                user_permissions.add(Permission(scope))
            except ValueError:
                pass

        has_perm = permission in user_permissions
        if not has_perm:
            self._log.warning(
                "authz.denied",
                user_id=user.get("id", "unknown"),
                permission=permission.value,
            )
        return has_perm

    def require(
        self,
        user: dict[str, Any],
        permission: Permission,
    ) -> None:
        """Require a permission, raising if the user lacks it.

        Args:
            user: The user dict.
            permission: The required permission.

        Raises:
            PermissionError: If the user lacks the permission.
        """
        if not self.check(user, permission):
            raise PermissionError(
                f"User '{user.get('id', 'unknown')}' lacks permission: {permission.value}"
            )
