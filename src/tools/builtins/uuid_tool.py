"""
Builtin tool: UUID Generator.

Tool ID: ``util.uuid.gen``
"""

from __future__ import annotations
import uuid
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="util.uuid.gen", execution_id=context.execution_id, agent_id=context.agent_id)
    count = payload.get("count", 1)
    version = payload.get("version", 4)
    uppercase = payload.get("uppercase", False)
    
    if count > 100:
        raise ValueError("Count capped at 100")
        
    uuids = []
    for _ in range(count):
        if version == 4:
            val = str(uuid.uuid4())
        elif version == 1:
            val = str(uuid.uuid1())
        else:
            raise ValueError(f"Unsupported UUID version: {version}")
        if uppercase:
            val = val.upper()
        uuids.append(val)
        
    return {
        "uuids": uuids,
        "count": count,
        "version": version
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="util.uuid.gen",
        name="UUID Generator",
        version="1.0.0",
        description="Generates UUIDs.",
        capabilities=["uuid", "generate", "identifier", "unique"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {
                "count": {"type": "integer", "default": 1, "maximum": 100},
                "version": {"type": "integer", "default": 4},
                "uppercase": {"type": "boolean", "default": False},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["uuids", "count", "version"],
            "properties": {
                "uuids": {"type": "array", "items": {"type": "string"}},
                "count": {"type": "integer"},
                "version": {"type": "integer"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

UUID_TOOL_MANIFEST = get_manifest()
