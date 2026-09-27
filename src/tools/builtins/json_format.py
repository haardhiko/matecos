"""
Builtin tool: JSON format.

Tool ID: ``text.json.format``
"""

from __future__ import annotations
import json
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.json.format", execution_id=context.execution_id, agent_id=context.agent_id)
    text = payload.get("text", "")
    indent = payload.get("indent", 2)
    sort_keys = payload.get("sort_keys", False)
    minify = payload.get("minify", False)
    
    if len(text) > 1024 * 1024:
        raise ValueError("JSON input exceeds maximum length of 1MB.")
        
    try:
        data = json.loads(text)
        valid = True
        error = None
        key_count = len(data) if isinstance(data, dict) else len(data) if isinstance(data, list) else 0
        data_type = type(data).__name__
        
        if minify:
            formatted = json.dumps(data, separators=(',', ':'), sort_keys=sort_keys)
        else:
            formatted = json.dumps(data, indent=indent, sort_keys=sort_keys)
    except json.JSONDecodeError as exc:
        formatted = ""
        valid = False
        error = str(exc)
        key_count = 0
        data_type = "unknown"
        log.warning("json_format.error", error=error)
        
    return {
        "formatted": formatted,
        "valid": valid,
        "error": error,
        "key_count": key_count,
        "type": data_type
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.json.format",
        name="JSON Format",
        version="1.0.0",
        description="Validates JSON, formats/minifies it.",
        capabilities=["json", "format", "validate", "minify"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "indent": {"type": "integer", "default": 2},
                "sort_keys": {"type": "boolean", "default": False},
                "minify": {"type": "boolean", "default": False},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["formatted", "valid", "error", "key_count", "type"],
            "properties": {
                "formatted": {"type": "string"},
                "valid": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
                "key_count": {"type": "integer"},
                "type": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

JSON_FORMAT_MANIFEST = get_manifest()
