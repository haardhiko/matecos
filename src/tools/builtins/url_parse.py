"""
Builtin tool: URL Parse.

Tool ID: ``text.url.parse``
"""

from __future__ import annotations
import urllib.parse
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.url.parse", execution_id=context.execution_id, agent_id=context.agent_id)
    url = payload.get("url", "")
    
    parsed = urllib.parse.urlparse(url)
    
    query = urllib.parse.parse_qs(parsed.query)
    
    masked_pw = None
    if parsed.password:
        masked_pw = "***"
        
    return {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "hostname": parsed.hostname or "",
        "port": parsed.port,
        "path": parsed.path,
        "query": query,
        "fragment": parsed.fragment,
        "username": parsed.username,
        "password": masked_pw,
        "params": parsed.params
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.url.parse",
        name="URL Parse",
        version="1.0.0",
        description="Parses URLs.",
        capabilities=["url", "parse", "query_string", "uri"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["url"],
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["scheme", "netloc", "hostname", "port", "path", "query", "fragment", "username", "password", "params"],
            "properties": {
                "scheme": {"type": "string"},
                "netloc": {"type": "string"},
                "hostname": {"type": "string"},
                "port": {"type": ["integer", "null"]},
                "path": {"type": "string"},
                "query": {"type": "object"},
                "fragment": {"type": "string"},
                "username": {"type": ["string", "null"]},
                "password": {"type": ["string", "null"]},
                "params": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

URL_PARSE_MANIFEST = get_manifest()
