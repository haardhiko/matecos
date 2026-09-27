"""
Builtin tool: Base64 Codec.

Tool ID: ``text.base64.codec``
"""

from __future__ import annotations
import base64
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.base64.codec", execution_id=context.execution_id, agent_id=context.agent_id)
    text = payload.get("text", "")
    action = payload.get("action", "encode")
    
    input_length = len(text)
    
    try:
        if action == "encode":
            result = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        elif action == "decode":
            result = base64.b64decode(text.encode("utf-8")).decode("utf-8")
        else:
            raise ValueError(f"Invalid action: {action}")
    except Exception as exc:
        raise ValueError(f"Base64 error: {exc}")
        
    return {
        "result": result,
        "action": action,
        "input_length": input_length,
        "output_length": len(result)
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.base64.codec",
        name="Base64 Codec",
        version="1.0.0",
        description="Encode or decode Base64 strings.",
        capabilities=["base64", "encode", "decode", "encoding"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text", "action"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "action": {"type": "string", "enum": ["encode", "decode"]},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "action", "input_length", "output_length"],
            "properties": {
                "result": {"type": "string"},
                "action": {"type": "string"},
                "input_length": {"type": "integer"},
                "output_length": {"type": "integer"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

BASE64_TOOL_MANIFEST = get_manifest()
