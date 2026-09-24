"""
test_security.py
================
Security tests covering authorization (RBAC), secrets management, and container isolation guarantees.
"""

from __future__ import annotations

import pytest

from src.security.authorization import AuthorizationService, Permission
from src.security.isolation import IsolationManager
from src.security.secrets import SecretNotFoundError, SecretsManager


class TestAuthorization:
    def test_admin_has_all_permissions(self) -> None:
        service = AuthorizationService()
        admin_user = {"id": "u-admin", "scopes": ["admin"]}
        for perm in Permission:
            assert service.check(admin_user, perm) is True

    def test_viewer_restricted_permissions(self) -> None:
        service = AuthorizationService()
        viewer_user = {"id": "u-viewer", "scopes": ["viewer"]}
        assert service.check(viewer_user, Permission.VIEW_STATUS) is True
        assert service.check(viewer_user, Permission.VIEW_TOOLS) is True
        assert service.check(viewer_user, Permission.SUBMIT_REQUEST) is False
        assert service.check(viewer_user, Permission.CANCEL_EXECUTION) is False

    def test_user_permissions(self) -> None:
        service = AuthorizationService()
        regular_user = {"id": "u-reg", "scopes": ["user"]}
        assert service.check(regular_user, Permission.SUBMIT_REQUEST) is True
        assert service.check(regular_user, Permission.REGISTER_TOOL) is False

    def test_require_raises_on_unauthorized(self) -> None:
        service = AuthorizationService()
        viewer = {"id": "u-viewer", "scopes": ["viewer"]}
        with pytest.raises(PermissionError, match="lacks permission"):
            service.require(viewer, Permission.REGISTER_TOOL)


class TestSecretsManager:
    def test_get_secret_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MATECOS_SECRET_TEST_TOKEN", "super-secret-xyz")
        manager = SecretsManager()
        val = manager.get("TEST_TOKEN")
        assert val == "super-secret-xyz"

    def test_get_secret_not_found(self) -> None:
        manager = SecretsManager()
        with pytest.raises(SecretNotFoundError, match="Secret 'MISSING' not found"):
            manager.get("MISSING")

    def test_get_or_default(self) -> None:
        manager = SecretsManager()
        val = manager.get_or_default("MISSING", default="fallback")
        assert val == "fallback"


class TestIsolationManager:
    def test_network_none_invariant_in_docker_args(self) -> None:
        manager = IsolationManager()
        for risk in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            policy = manager.get_policy("tool.test", risk_level=risk)
            args = manager.build_docker_args(policy)
            # HARD INVARIANT: '--network none' MUST be present in docker args
            assert "--network" in args
            net_idx = args.index("--network")
            assert args[net_idx + 1] == "none"

    def test_strict_isolation_for_critical_risk(self) -> None:
        manager = IsolationManager()
        policy = manager.get_policy("tool.dangerous", risk_level="CRITICAL")
        assert policy.readonly_rootfs is True
        assert policy.max_memory_mb <= 1024
        assert policy.max_cpu_percent <= 25

    def test_no_new_privileges_flag_present(self) -> None:
        manager = IsolationManager()
        policy = manager.get_policy("tool.test", risk_level="LOW")
        args = manager.build_docker_args(policy)
        assert "--security-opt" in args
        sec_idx = args.index("--security-opt")
        assert args[sec_idx + 1] == "no-new-privileges"
