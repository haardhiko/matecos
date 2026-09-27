"""
Builtin tool: Password Gen.

Tool ID: ``util.password.gen``
"""

from __future__ import annotations
import secrets
import string
import math
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="util.password.gen", execution_id=context.execution_id, agent_id=context.agent_id)
    length = min(payload.get("length", 16), 128)
    count = min(payload.get("count", 1), 20)
    uppercase = payload.get("uppercase", True)
    lowercase = payload.get("lowercase", True)
    digits = payload.get("digits", True)
    symbols = payload.get("symbols", True)
    exclude = payload.get("exclude", "")
    
    chars = ""
    if uppercase: chars += string.ascii_uppercase
    if lowercase: chars += string.ascii_lowercase
    if digits: chars += string.digits
    if symbols: chars += string.punctuation
    
    chars = "".join(c for c in chars if c not in exclude)
    if not chars:
        raise ValueError("No characters available")
        
    passwords = []
    for _ in range(count):
        pw = "".join(secrets.choice(chars) for _ in range(length))
        passwords.append(pw)
        
    entropy = length * math.log2(len(chars)) if chars else 0
    if entropy < 40:
        strength = "weak"
    elif entropy < 60:
        strength = "medium"
    elif entropy < 80:
        strength = "strong"
    else:
        strength = "very_strong"
        
    return {
        "passwords": passwords,
        "length": length,
        "strength": strength,
        "entropy_bits": entropy
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="util.password.gen",
        name="Password Generator",
        version="1.0.0",
        description="Generates secure passwords.",
        capabilities=["password", "generate", "security", "random"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": [],
            "additionalProperties": False,
            "properties": {
                "length": {"type": "integer", "default": 16, "maximum": 128},
                "count": {"type": "integer", "default": 1, "maximum": 20},
                "uppercase": {"type": "boolean", "default": True},
                "lowercase": {"type": "boolean", "default": True},
                "digits": {"type": "boolean", "default": True},
                "symbols": {"type": "boolean", "default": True},
                "exclude": {"type": "string", "default": ""},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["passwords", "length", "strength", "entropy_bits"],
            "properties": {
                "passwords": {"type": "array", "items": {"type": "string"}},
                "length": {"type": "integer"},
                "strength": {"type": "string"},
                "entropy_bits": {"type": "number"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

PASSWORD_GEN_MANIFEST = get_manifest()
