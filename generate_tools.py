import os
from pathlib import Path

TOOLS_DIR = Path("C:/Users/haard/.gemini/antigravity/scratch/matecos/src/tools/builtins")

JSON_FORMAT_CODE = '''"""
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
'''

BASE64_CODE = '''"""
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
'''

HASH_CODE = '''"""
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
'''

UUID_CODE = '''"""
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
'''

REGEX_CODE = '''"""
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
'''

TIMESTAMP_CODE = '''"""
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
'''

WORD_COUNT_CODE = '''"""
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
    characters_no_spaces = len(text.replace(" ", "").replace("\\n", "").replace("\\t", "").replace("\\r", ""))
    words = len(text.split())
    lines = len(text.splitlines())
    sentences = text.count(".") + text.count("!") + text.count("?") # basic approximation
    paragraphs = len([p for p in text.split("\\n\\n") if p.strip()])
    
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
'''

URL_PARSE_CODE = '''"""
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
'''

PASSWORD_GEN_CODE = '''"""
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
'''

TEXT_DIFF_CODE = '''"""
Builtin tool: Text Diff.

Tool ID: ``text.diff.compare``
"""

from __future__ import annotations
import difflib
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="text.diff.compare", execution_id=context.execution_id, agent_id=context.agent_id)
    text_a = payload.get("text_a", "")
    text_b = payload.get("text_b", "")
    context_lines = payload.get("context_lines", 3)
    
    a_lines = text_a.splitlines(keepends=True)
    b_lines = text_b.splitlines(keepends=True)
    
    diff = "".join(difflib.unified_diff(a_lines, b_lines, n=context_lines))
    
    additions = sum(1 for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++'))
    deletions = sum(1 for line in diff.splitlines() if line.startswith('-') and not line.startswith('---'))
    changes = additions + deletions
    
    matcher = difflib.SequenceMatcher(None, text_a, text_b)
    similarity_ratio = matcher.ratio()
    
    return {
        "diff": diff,
        "additions": additions,
        "deletions": deletions,
        "changes": changes,
        "similarity_ratio": similarity_ratio
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="text.diff.compare",
        name="Text Diff",
        version="1.0.0",
        description="Compares texts.",
        capabilities=["diff", "compare", "text_comparison", "changes"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["text_a", "text_b"],
            "additionalProperties": False,
            "properties": {
                "text_a": {"type": "string"},
                "text_b": {"type": "string"},
                "context_lines": {"type": "integer", "default": 3},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["diff", "additions", "deletions", "changes", "similarity_ratio"],
            "properties": {
                "diff": {"type": "string"},
                "additions": {"type": "integer"},
                "deletions": {"type": "integer"},
                "changes": {"type": "integer"},
                "similarity_ratio": {"type": "number"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

TEXT_DIFF_MANIFEST = get_manifest()
'''

ENCODE_TOOL_CODE = '''"""
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
'''

UNIT_CONVERT_CODE = '''"""
Builtin tool: Unit Convert.

Tool ID: ``data.convert.units``
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import ResourceLimits, RuntimeConfig, SideEffects, ToolManifest

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

async def handler(payload: dict, context: ToolExecutionContext) -> dict:
    log = logger.bind(tool_id="data.convert.units", execution_id=context.execution_id, agent_id=context.agent_id)
    value = float(payload.get("value", 0))
    from_unit = payload.get("from_unit", "").lower()
    to_unit = payload.get("to_unit", "").lower()
    
    result = 0.0
    formula = f"{value} {from_unit} -> {to_unit}"
    
    # temp
    if from_unit in ['c', 'f', 'k'] and to_unit in ['c', 'f', 'k']:
        c = 0
        if from_unit == 'c': c = value
        elif from_unit == 'f': c = (value - 32) * 5/9
        elif from_unit == 'k': c = value - 273.15
        
        if to_unit == 'c': result = c
        elif to_unit == 'f': result = (c * 9/5) + 32
        elif to_unit == 'k': result = c + 273.15
    # length (base m)
    elif from_unit in ['m', 'km', 'mi', 'ft', 'in', 'cm', 'mm', 'yd'] and to_unit in ['m', 'km', 'mi', 'ft', 'in', 'cm', 'mm', 'yd']:
        factors = {'m': 1, 'km': 1000, 'mi': 1609.34, 'ft': 0.3048, 'in': 0.0254, 'cm': 0.01, 'mm': 0.001, 'yd': 0.9144}
        m = value * factors[from_unit]
        result = m / factors[to_unit]
    # weight (base kg)
    elif from_unit in ['kg', 'lb', 'oz', 'g', 'mg', 'ton'] and to_unit in ['kg', 'lb', 'oz', 'g', 'mg', 'ton']:
        factors = {'kg': 1, 'lb': 0.453592, 'oz': 0.0283495, 'g': 0.001, 'mg': 0.000001, 'ton': 1000}
        kg = value * factors[from_unit]
        result = kg / factors[to_unit]
    # data (base b)
    elif from_unit in ['b', 'kb', 'mb', 'gb', 'tb'] and to_unit in ['b', 'kb', 'mb', 'gb', 'tb']:
        factors = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4}
        b = value * factors[from_unit]
        result = b / factors[to_unit]
    # time (base s)
    elif from_unit in ['s', 'min', 'h', 'day', 'week', 'month', 'year'] and to_unit in ['s', 'min', 'h', 'day', 'week', 'month', 'year']:
        factors = {'s': 1, 'min': 60, 'h': 3600, 'day': 86400, 'week': 604800, 'month': 2629800, 'year': 31557600}
        s = value * factors[from_unit]
        result = s / factors[to_unit]
    else:
        raise ValueError(f"Incompatible or unknown units: {from_unit} to {to_unit}")
        
    return {
        "result": result,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "formula": formula
    }

def get_manifest() -> ToolManifest:
    return ToolManifest(
        tool_id="data.convert.units",
        name="Unit Converter",
        version="1.0.0",
        description="Converts units.",
        capabilities=["convert", "units", "measurement", "temperature", "length", "weight"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["value", "from_unit", "to_unit"],
            "additionalProperties": False,
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["result", "from_unit", "to_unit", "formula"],
            "properties": {
                "result": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
                "formula": {"type": "string"},
            },
        },
        risk_level="low",
        side_effects=SideEffects.NONE,
        permissions=[],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(timeout_seconds=5, max_memory_mb=32, max_output_kb=16),
    )

UNIT_CONVERT_MANIFEST = get_manifest()
'''

FILES = {
    "json_format.py": JSON_FORMAT_CODE,
    "base64_tool.py": BASE64_CODE,
    "hash_tool.py": HASH_CODE,
    "uuid_tool.py": UUID_CODE,
    "regex_tool.py": REGEX_CODE,
    "timestamp_tool.py": TIMESTAMP_CODE,
    "word_count.py": WORD_COUNT_CODE,
    "url_parse.py": URL_PARSE_CODE,
    "password_gen.py": PASSWORD_GEN_CODE,
    "text_diff.py": TEXT_DIFF_CODE,
    "encode_tool.py": ENCODE_TOOL_CODE,
    "unit_convert.py": UNIT_CONVERT_CODE,
}

if not TOOLS_DIR.exists():
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

for fname, content in FILES.items():
    (TOOLS_DIR / fname).write_text(content, encoding="utf-8")
    
print("Generated all files")
