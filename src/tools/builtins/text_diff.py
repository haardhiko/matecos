"""
Builtin tool: Text Diff.

Tool ID: ``text.diff.compare``
"""

from __future__ import annotations
import difflib
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.diff.compare", execution_id=context.execution_id, agent_id=context.agent_id)
    text_a = payload.get("text_a", "")
    text_b = payload.get("text_b", "")
    context_lines = payload.get("context_lines", 3)
    
    a_lines = text_a.splitlines(keepends=True)
    b_lines = text_b.splitlines(keepends=True)
    
    diff = "".join(difflib.unified_diff(a_lines, b_lines, n=context_lines))
    
    additions = sum(1 for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++'))
    deletions = sum(1 for line in diff.splitlines() if line.startswith('-') and not line.startswith('---'))
    changes = additions + deletions
    
    matcher = difflib.SequenceMatcher(None, text_a, text_b)
    similarity_ratio = matcher.ratio()
    
    return {
        "diff": diff,
        "additions": additions,
        "deletions": deletions,
        "changes": changes,
        "similarity_ratio": similarity_ratio
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.diff.compare",
        name="Text Diff",
        version="1.0.0",
        description="Compares texts.",
        capabilities=["diff", "compare", "text_comparison", "changes"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text_a", "text_b"],
            "additionalProperties": False,
            "properties": {
                "text_a": {"type": "string"},
                "text_b": {"type": "string"},
                "context_lines": {"type": "integer", "default": 3},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["diff", "additions", "deletions", "changes", "similarity_ratio"],
            "properties": {
                "diff": {"type": "string"},
                "additions": {"type": "integer"},
                "deletions": {"type": "integer"},
                "changes": {"type": "integer"},
                "similarity_ratio": {"type": "number"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

TEXT_DIFF_MANIFEST = get_manifest()
