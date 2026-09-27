"""
Builtin tool: Timestamp Tool.

Tool ID: ``util.timestamp.convert``
"""

from __future__ import annotations
import datetime
from email.utils import format_datetime
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="util.timestamp.convert", execution_id=context.execution_id, agent_id=context.agent_id)
    value = payload.get("value")
    from_format = payload.get("from_format", "auto")
    to_format = payload.get("to_format", "iso8601")
    
    dt = None
    try:
        if from_format == "epoch" or from_format == "auto" and isinstance(value, (int, float)):
            dt = datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
            from_format = "epoch"
        elif from_format == "epoch_ms":
            dt = datetime.datetime.fromtimestamp(float(value)/1000.0, tz=datetime.timezone.utc)
        elif from_format == "iso8601" or from_format == "auto" and isinstance(value, str):
            dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            from_format = "iso8601"
        else:
            dt = datetime.datetime.strptime(str(value), from_format)
            dt = dt.replace(tzinfo=datetime.timezone.utc)
    except Exception as exc:
        raise ValueError(f"Parse error: {exc}")
        
    if dt is None:
        raise ValueError("Could not parse timestamp")
        
    epoch = dt.timestamp()
    iso8601 = dt.isoformat()
    human = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    if to_format == "epoch":
        result = str(epoch)
    elif to_format == "epoch_ms":
        result = str(int(epoch * 1000))
    elif to_format == "iso8601":
        result = iso8601
    elif to_format == "human":
        result = human
    elif to_format == "rfc2822":
        result = format_datetime(dt)
    else:
        result = dt.strftime(to_format)
        
    return {
        "result": result,
        "from_format": from_format,
        "to_format": to_format,
        "epoch": epoch,
        "iso8601": iso8601,
        "human": human
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="util.timestamp.convert",
        name="Timestamp Converter",
        version="1.0.0",
        description="Converts timestamps.",
        capabilities=["timestamp", "datetime", "convert", "timezone", "epoch"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["value"],
            "additionalProperties": False,
            "properties": {
                "value": {},
                "from_format": {"type": "string", "default": "auto"},
                "to_format": {"type": "string", "default": "iso8601"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "from_format", "to_format", "epoch", "iso8601", "human"],
            "properties": {
                "result": {"type": "string"},
                "from_format": {"type": "string"},
                "to_format": {"type": "string"},
                "epoch": {"type": "number"},
                "iso8601": {"type": "string"},
                "human": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

TIMESTAMP_TOOL_MANIFEST = get_manifest()
