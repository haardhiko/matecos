"""
test_import_e2e.py
==================
End-to-end integration test verifying:
FETCH -> DISCOVER -> NORMALIZE -> MANIFEST -> REGISTER
works on a real repository with the UniversalImporter.
"""

from __future__ import annotations

import pytest

from src.tools.executor import ToolExecutor
from src.tools.id_generator import is_valid_tool_id
from src.tools.importer.pipeline import UniversalImporter
from src.tools.registry import ToolRegistry


class TestUniversalImporterE2E:
    @pytest.mark.asyncio
    async def test_real_repo_import_end_to_end(self):
        """Verify real Git repository import end-to-end."""
        registry = ToolRegistry()
        executor = ToolExecutor()
        importer = UniversalImporter()

        # Import a real public repository
        report = await importer.import_repository(
            "https://github.com/bottlepy/bottle",
            branch="master",
            registry=registry,
            executor=executor,
        )

        assert report.status == "success"
        assert report.tools_discovered >= 1
        assert report.tools_registered >= 1
        assert report.tools_failed == 0
        assert len(report.errors) == 0

        # Verify all registered tool IDs are strictly valid and exist in the registry
        for tool_info in report.tools:
            tool_id = tool_info["tool_id"]
            assert is_valid_tool_id(tool_id), f"Tool ID '{tool_id}' failed regex validation"
            manifest = registry.get(tool_id)
            assert manifest is not None
            assert manifest.tool_id == tool_id
