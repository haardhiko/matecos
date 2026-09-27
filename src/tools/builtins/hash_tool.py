"""
Builtin tool: Hash Generator.

Tool ID: ``crypto.hash.gen``
"""

from __future__ import annotations
import hashlib
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="crypto.hash.gen", execution_id=context.execution_id, agent_id=context.agent_id)
    text = payload.get("text", "")
    algorithm = payload.get("algorithm", "sha256")
    
    if algorithm not in ["md5", "sha1", "sha256", "sha512"]:
        raise ValueError(f"Unsupported algorithm: {algorithm}")
        
    try:
        h = hashlib.new(algorithm)
        h.update(text.encode("utf-8"))
        result = h.hexdigest()
    except Exception as exc:
        raise ValueError(f"Hash error: {exc}")
        
    return {
        "hash": result,
        "algorithm": algorithm,
        "length": len(result)
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="crypto.hash.gen",
        name="Hash Generator",
        version="1.0.0",
        description="Generates hash values.",
        capabilities=["hash", "sha256", "md5", "checksum", "crypto"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"], "default": "sha256"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["hash", "algorithm", "length"],
            "properties": {
                "hash": {"type": "string"},
                "algorithm": {"type": "string"},
                "length": {"type": "integer"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

HASH_TOOL_MANIFEST = get_manifest()
