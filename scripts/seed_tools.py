"""
seed_tools.py
=============
Seeds the static tool registry with all builtin tool manifests and handlers.
"""

from __future__ import annotations

import structlog

from src.tools.builtins.calculator import CALCULATOR_MANIFEST, calculate
from src.tools.builtins.csv_profile import CSV_PROFILE_MANIFEST, profile_csv
from src.tools.builtins.http_fetch import HTTP_FETCH_MANIFEST, fetch_url
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


def build_default_registry() -> tuple[ToolRegistry, ToolExecutor]:
    """Instantiate and seed the default ToolRegistry and ToolExecutor.

    Returns:
        Tuple of (ToolRegistry, ToolExecutor) with all builtins registered.
    """
    registry = ToolRegistry()
    executor = ToolExecutor()

    # Register builtins: manifests in registry, handlers in executor
    registry.register(CALCULATOR_MANIFEST)
    executor.register_builtin("math.calculator", calculate)

    registry.register(CSV_PROFILE_MANIFEST)
    executor.register_builtin("data.csv.profile", profile_csv)

    registry.register(HTTP_FETCH_MANIFEST)
    executor.register_builtin("web.http_fetch", fetch_url)

    logger.info("tools.seeded", count=registry.tool_count)
    return registry, executor


if __name__ == "__main__":
    registry, _ = build_default_registry()
    print(f"Successfully seeded {registry.tool_count} tools into registry:")
    for record in registry.list_all():
        print(
            f" - [{record.manifest.risk_level.upper()}] {record.tool_id} (v{record.manifest.version}): {record.manifest.description}"
        )
