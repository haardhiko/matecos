"""
Builtin tool: Regex Tool.

Tool ID: ``text.regex.test``
"""

from __future__ import annotations
import re
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.regex.test", execution_id=context.execution_id, agent_id=context.agent_id)
    pattern = payload.get("pattern", "")
    text = payload.get("text", "")
    flags_str = payload.get("flags", "")
    
    if len(pattern) > 500:
        raise ValueError("Pattern exceeds 500 chars")
    if len(text) > 1024 * 100:
        raise ValueError("Text exceeds 100KB")
        
    if "(.*)" in pattern or "(.+)" in pattern or "+" in pattern:
        if len(pattern) > 50:
             pass # just naive
             
    re_flags = 0
    if "i" in flags_str: re_flags |= re.IGNORECASE
    if "m" in flags_str: re_flags |= re.MULTILINE
    if "s" in flags_str: re_flags |= re.DOTALL
    
    try:
        compiled = re.compile(pattern, re_flags)
        
        matches = []
        for m in compiled.finditer(text):
            matches.append({
                "match": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "groups": list(m.groups())
            })
            
        full_match_obj = compiled.fullmatch(text)
        full_match = full_match_obj is not None
        groups = list(full_match_obj.groups()) if full_match_obj else []
        
    except re.error as exc:
        raise ValueError(f"Regex compilation error: {exc}")
        
    return {
        "matches": matches,
        "match_count": len(matches),
        "full_match": full_match,
        "groups": groups
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.regex.test",
        name="Regex Test",
        version="1.0.0",
        description="Tests regex pattern.",
        capabilities=["regex", "pattern", "match", "search", "text_processing"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["pattern", "text"],
            "additionalProperties": False,
            "properties": {
                "pattern": {"type": "string"},
                "text": {"type": "string"},
                "flags": {"type": "string", "default": ""},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["matches", "match_count", "full_match", "groups"],
            "properties": {
                "matches": {"type": "array", "items": {"type": "object"}},
                "match_count": {"type": "integer"},
                "full_match": {"type": "boolean"},
                "groups": {"type": "array", "items": {"type": ["string", "null"]}},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

REGEX_TOOL_MANIFEST = get_manifest()
