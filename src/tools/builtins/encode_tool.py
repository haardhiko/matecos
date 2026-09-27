"""
Builtin tool: Encode.

Tool ID: ``text.encode.convert``
"""

from __future__ import annotations
import urllib.parse
import html
import codecs
import base64
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.encode.convert", execution_id=context.execution_id, agent_id=context.agent_id)
    text = payload.get("text", "")
    action = payload.get("action", "encode")
    encoding = payload.get("encoding", "url")
    
    result = ""
    try:
        if encoding == "url":
            if action == "encode":
                result = urllib.parse.quote(text)
            else:
                result = urllib.parse.unquote(text)
        elif encoding == "html":
            if action == "encode":
                result = html.escape(text)
            else:
                result = html.unescape(text)
        elif encoding == "hex":
            if action == "encode":
                result = text.encode('utf-8').hex()
            else:
                result = bytes.fromhex(text).decode('utf-8')
        elif encoding == "rot13":
            result = codecs.encode(text, 'rot_13')
        elif encoding == "ascii85":
            if action == "encode":
                result = base64.a85encode(text.encode('utf-8')).decode('utf-8')
            else:
                result = base64.a85decode(text.encode('utf-8')).decode('utf-8')
        else:
            raise ValueError(f"Unknown encoding: {encoding}")
    except Exception as exc:
        raise ValueError(f"Encoding error: {exc}")
        
    return {
        "result": result,
        "encoding": encoding,
        "action": action
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.encode.convert",
        name="Encoder",
        version="1.0.0",
        description="Encodes/decodes texts.",
        capabilities=["encode", "decode", "url_encode", "html_encode", "hex"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text", "action"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "action": {"type": "string", "enum": ["encode", "decode"]},
                "encoding": {"type": "string", "enum": ["url", "html", "hex", "rot13", "ascii85"], "default": "url"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "encoding", "action"],
            "properties": {
                "result": {"type": "string"},
                "encoding": {"type": "string"},
                "action": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

ENCODE_TOOL_MANIFEST = get_manifest()
