"""
test_contract.py
================
Schema and contract verification tests (RFC 9457, tool manifests, Pydantic contracts).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas.common import ErrorDetail
from src.tools.manifests import (
    FilesystemPolicy,
    NetworkPolicy,
    ResourceLimits,
    RuntimeConfig,
    SecurityPolicy,
)
from src.tools.manifests import (
    ToolManifest as DomainToolManifest,
)


class TestRFC9457Contract:
    def test_error_detail_schema(self) -> None:
        err = ErrorDetail(
            type="https://example.com/errors/invalid-input",
            title="Validation Failure",
            status=422,
            detail="Field 'text' is too short",
            instance="/v1/requests/req-123",
            extensions={"error_code": "TEXT_TOO_SHORT"},
        )
        data = err.model_dump()
        assert data["type"] == "https://example.com/errors/invalid-input"
        assert data["title"] == "Validation Failure"
        assert data["status"] == 422
        assert data["detail"] == "Field 'text' is too short"
        assert data["instance"] == "/v1/requests/req-123"
        assert data["extensions"]["error_code"] == "TEXT_TOO_SHORT"


class TestToolManifestContract:
    def test_manifest_tool_id_regex_validation(self) -> None:
        # Valid ID: lowercase dot-separated segments
        manifest = DomainToolManifest(
            tool_id="system.data.profiler",
            name="profiler",
            description="Profiles data",
            version="1.0.0",
            owner="core",
            capabilities=["profile"],
            risk_level="low",
            runtime=RuntimeConfig(type="builtin"),
            resource_limits=ResourceLimits(),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert manifest.tool_id == "system.data.profiler"

        # Invalid ID: contains uppercase
        with pytest.raises(ValidationError):
            DomainToolManifest(
                tool_id="System.Data.Profiler",
                name="bad",
                description="bad",
                version="1.0.0",
                owner="core",
                capabilities=["profile"],
                risk_level="low",
                runtime=RuntimeConfig(type="builtin"),
                resource_limits=ResourceLimits(),
                input_schema={},
                output_schema={},
            )

    def test_high_risk_requires_scan_passed(self) -> None:
        # High risk requires scan_passed=True in security policy
        with pytest.raises(
            ValidationError, match="High-risk and critical tools must pass security scanning"
        ):
            DomainToolManifest(
                tool_id="system.code.exec",
                name="exec",
                description="Code execution",
                version="1.0.0",
                owner="core",
                capabilities=["code_exec"],
                risk_level="high",
                runtime=RuntimeConfig(type="container", image="ubuntu:latest"),
                limits=ResourceLimits(),
                security=SecurityPolicy(
                    network=NetworkPolicy.DENY_ALL,
                    filesystem=FilesystemPolicy.READ_ONLY,
                    scan_passed=False,  # Should fail validation!
                ),
                input_schema={},
                output_schema={},
            )
