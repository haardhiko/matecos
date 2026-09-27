"""
Builtin tool: Word Count.

Tool ID: ``text.word.count``
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.word.count", execution_id=context.execution_id, agent_id=context.agent_id)
    text = payload.get("text", "")
    
    characters = len(text)
    characters_no_spaces = len(text.replace(" ", "").replace("\n", "").replace("\t", "").replace("\r", ""))
    words = len(text.split())
    lines = len(text.splitlines())
    sentences = text.count(".") + text.count("!") + text.count("?") # basic approximation
    paragraphs = len([p for p in text.split("\n\n") if p.strip()])
    
    avg_word_length = 0.0
    if words > 0:
        avg_word_length = sum(len(w) for w in text.split()) / words
        
    reading_time_seconds = int((words / 200.0) * 60)
    
    return {
        "characters": characters,
        "characters_no_spaces": characters_no_spaces,
        "words": words,
        "sentences": sentences,
        "paragraphs": paragraphs,
        "lines": lines,
        "avg_word_length": avg_word_length,
        "reading_time_seconds": reading_time_seconds
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.word.count",
        name="Word Count",
        version="1.0.0",
        description="Text statistics.",
        capabilities=["word_count", "character_count", "text_analysis", "statistics"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["characters", "characters_no_spaces", "words", "sentences", "paragraphs", "lines", "avg_word_length", "reading_time_seconds"],
            "properties": {
                "characters": {"type": "integer"},
                "characters_no_spaces": {"type": "integer"},
                "words": {"type": "integer"},
                "sentences": {"type": "integer"},
                "paragraphs": {"type": "integer"},
                "lines": {"type": "integer"},
                "avg_word_length": {"type": "number"},
                "reading_time_seconds": {"type": "integer"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

WORD_COUNT_MANIFEST = get_manifest()
