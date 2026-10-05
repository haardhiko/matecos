"""
mcp_server.py
=============
MATECOS Model Context Protocol (MCP) Server for OpenCode and other MCP clients.
Provides direct access to all 15 builtin tools, GitHub repository importing,
task orchestration, and dynamic capability execution.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# CRITICAL: stdio MCP requires stdout to be pure JSON-RPC. Divert all logs to stderr!
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
import structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

from mcp.server.mcpserver import MCPServer
from src.tools.registry import ToolExecutionContext, ToolRegistry
from src.tools.executor import ToolExecutor

# --- Bootstrap Tool Registry ---
from src.tools.builtins.calculator import calculator_handler, get_manifest as calc_manifest
from src.tools.builtins.http_fetch import http_fetch_handler, get_manifest as http_manifest
from src.tools.builtins.csv_profile import csv_profile_handler, get_manifest as csv_manifest
from src.tools.builtins.json_format import handler as json_format_handler, JSON_FORMAT_MANIFEST
from src.tools.builtins.base64_tool import handler as base64_tool_handler, BASE64_TOOL_MANIFEST
from src.tools.builtins.hash_tool import handler as hash_tool_handler, HASH_TOOL_MANIFEST
from src.tools.builtins.uuid_tool import handler as uuid_tool_handler, UUID_TOOL_MANIFEST
from src.tools.builtins.regex_tool import handler as regex_tool_handler, REGEX_TOOL_MANIFEST
from src.tools.builtins.timestamp_tool import handler as timestamp_tool_handler, TIMESTAMP_TOOL_MANIFEST
from src.tools.builtins.word_count import handler as word_count_handler, WORD_COUNT_MANIFEST
from src.tools.builtins.url_parse import handler as url_parse_handler, URL_PARSE_MANIFEST
from src.tools.builtins.password_gen import handler as password_gen_handler, PASSWORD_GEN_MANIFEST
from src.tools.builtins.text_diff import handler as text_diff_handler, TEXT_DIFF_MANIFEST
from src.tools.builtins.encode_tool import handler as encode_tool_handler, ENCODE_TOOL_MANIFEST
from src.tools.builtins.unit_convert import handler as unit_convert_handler, UNIT_CONVERT_MANIFEST

registry = ToolRegistry()
executor = ToolExecutor()

manifests = [
    calc_manifest(),
    http_manifest(),
    csv_manifest(),
    JSON_FORMAT_MANIFEST,
    BASE64_TOOL_MANIFEST,
    HASH_TOOL_MANIFEST,
    UUID_TOOL_MANIFEST,
    REGEX_TOOL_MANIFEST,
    TIMESTAMP_TOOL_MANIFEST,
    WORD_COUNT_MANIFEST,
    URL_PARSE_MANIFEST,
    PASSWORD_GEN_MANIFEST,
    TEXT_DIFF_MANIFEST,
    ENCODE_TOOL_MANIFEST,
    UNIT_CONVERT_MANIFEST,
]

handlers = {
    "math.calculator": calculator_handler,
    "web.http_fetch": http_fetch_handler,
    "data.csv.profile": csv_profile_handler,
    "text.json.format": json_format_handler,
    "text.base64.codec": base64_tool_handler,
    "crypto.hash.gen": hash_tool_handler,
    "util.uuid.gen": uuid_tool_handler,
    "text.regex.test": regex_tool_handler,
    "util.timestamp.convert": timestamp_tool_handler,
    "text.word.count": word_count_handler,
    "text.url.parse": url_parse_handler,
    "util.password.gen": password_gen_handler,
    "text.diff.compare": text_diff_handler,
    "text.encode.convert": encode_tool_handler,
    "data.convert.units": unit_convert_handler,
}

for m in manifests:
    registry.register(m)
for tid, h in handlers.items():
    executor.register_builtin(tid, h)


async def _exec(tool_id: str, payload: dict[str, Any]) -> str:
    ctx = ToolExecutionContext(execution_id="mcp", agent_id="opencode", user_id="user")
    rec = registry.get(tool_id)
    if not rec:
        return f"Error: Tool '{tool_id}' not found in MATECOS registry"
    try:
        res = await executor.execute(rec.manifest, payload, ctx)
        if res.status == "SUCCEEDED":
            return json.dumps(res.output, indent=2, default=str)
        return f"Error ({res.status}): {res.error}"
    except Exception as exc:
        return f"Error executing tool {tool_id}: {exc}"


# --- Initialize MCPServer ---
mcp_app = MCPServer("matecos")


# 1. Calculator
@mcp_app.tool(name="math_calculator", description="Safely evaluate mathematical and arithmetic expressions.")
async def math_calculator(expression: str) -> str:
    return await _exec("math.calculator", {"expression": expression})


# 2. HTTP Fetch
@mcp_app.tool(name="web_http_fetch", description="Perform HTTP requests with timeout, SSL verification, and redirect controls.")
async def web_http_fetch(url: str, method: str = "GET", headers: dict | None = None, body: str | None = None) -> str:
    payload: dict[str, Any] = {"url": url, "method": method}
    if headers:
        payload["headers"] = headers
    if body:
        payload["body"] = body
    return await _exec("web.http_fetch", payload)


# 3. CSV Profile
@mcp_app.tool(name="data_csv_profile", description="Analyze CSV data structure, column types, row counts, and summary statistics.")
async def data_csv_profile(csv_content: str) -> str:
    return await _exec("data.csv.profile", {"csv_content": csv_content})


# 4. JSON Format
@mcp_app.tool(name="text_json_format", description="Format, validate, or minify JSON data.")
async def text_json_format(json_string: str, indent: int = 2) -> str:
    return await _exec("text.json.format", {"json_string": json_string, "indent": indent})


# 5. Base64 Codec
@mcp_app.tool(name="text_base64_codec", description="Encode or decode Base64 strings.")
async def text_base64_codec(text: str, action: str = "encode") -> str:
    return await _exec("text.base64.codec", {"text": text, "action": action})


# 6. Hash Gen
@mcp_app.tool(name="crypto_hash_gen", description="Generate cryptographic hashes (MD5, SHA1, SHA256, SHA512).")
async def crypto_hash_gen(text: str, algorithm: str = "sha256") -> str:
    return await _exec("crypto.hash.gen", {"text": text, "algorithm": algorithm})


# 7. UUID Gen
@mcp_app.tool(name="util_uuid_gen", description="Generate random UUIDv4 identifiers.")
async def util_uuid_gen(count: int = 1) -> str:
    return await _exec("util.uuid.gen", {"count": count})


# 8. Regex Test
@mcp_app.tool(name="text_regex_test", description="Test regular expressions against text and extract capture groups.")
async def text_regex_test(pattern: str, text: str) -> str:
    return await _exec("text.regex.test", {"pattern": pattern, "text": text})


# 9. Timestamp Convert
@mcp_app.tool(name="util_timestamp_convert", description="Convert timestamps between ISO8601, Unix timestamps, and RFC2822.")
async def util_timestamp_convert(timestamp: str | None = None, from_format: str = "auto", to_format: str = "iso") -> str:
    return await _exec("util.timestamp.convert", {"timestamp": timestamp, "from_format": from_format, "to_format": to_format})


# 10. Word Count
@mcp_app.tool(name="text_word_count", description="Calculate text statistics: word count, character count, lines, reading time.")
async def text_word_count(text: str) -> str:
    return await _exec("text.word.count", {"text": text})


# 11. URL Parse
@mcp_app.tool(name="text_url_parse", description="Parse and decompose URLs into components (scheme, host, path, query params).")
async def text_url_parse(url: str) -> str:
    return await _exec("text.url.parse", {"url": url})


# 12. Password Gen
@mcp_app.tool(name="util_password_gen", description="Generate cryptographically secure passwords with custom entropy options.")
async def util_password_gen(length: int = 16, include_symbols: bool = True, include_numbers: bool = True) -> str:
    return await _exec("util.password.gen", {"length": length, "include_symbols": include_symbols, "include_numbers": include_numbers})


# 13. Text Diff
@mcp_app.tool(name="text_diff_compare", description="Generate unified diff between two text documents.")
async def text_diff_compare(original: str, modified: str) -> str:
    return await _exec("text.diff.compare", {"original": original, "modified": modified})


# 14. Encode Convert
@mcp_app.tool(name="text_encode_convert", description="Convert text encodings (UTF-8, ASCII, Hex, Binary).")
async def text_encode_convert(text: str, from_encoding: str = "utf-8", to_encoding: str = "hex") -> str:
    return await _exec("text.encode.convert", {"text": text, "from_encoding": from_encoding, "to_encoding": to_encoding})


# 15. Unit Convert
@mcp_app.tool(name="data_convert_units", description="Convert units across length, mass, temperature, digital storage, and speed.")
async def data_convert_units(value: float, from_unit: str, to_unit: str) -> str:
    return await _exec("data.convert.units", {"value": value, "from_unit": from_unit, "to_unit": to_unit})


# 16. Universal Tool Invoker
@mcp_app.tool(name="matecos_execute_tool", description="Execute any MATECOS tool by tool_id with arbitrary JSON parameters.")
async def matecos_execute_tool(tool_id: str, parameters_json: str = "{}") -> str:
    try:
        payload = json.loads(parameters_json)
    except json.JSONDecodeError as err:
        return f"Invalid JSON parameters: {err}"
    return await _exec(tool_id, payload)


# 17. List All Available MATECOS Tools
@mcp_app.tool(name="matecos_list_tools", description="List all registered MATECOS tools with their capabilities and risk levels.")
def matecos_list_tools() -> str:
    tools_info = []
    for r in registry.list_all():
        m = r.manifest
        tools_info.append({
            "tool_id": m.tool_id,
            "name": m.name,
            "description": m.description,
            "capabilities": m.capabilities,
            "risk_level": m.risk_level,
        })
    return json.dumps(tools_info, indent=2)


# 18. GitHub Repository Tool Importer
@mcp_app.tool(name="matecos_import_github_repo", description="Clone and import tools from a GitHub repository URL into MATECOS.")
async def matecos_import_github_repo(repo_url: str, branch: str = "main") -> str:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("http://127.0.0.1:8000/v1/github/import", json={"url": repo_url, "branch": branch})
            if resp.status_code == 200:
                return json.dumps(resp.json(), indent=2)
            return f"Import failed (HTTP {resp.status_code}): {resp.text}"
    except Exception as exc:
        return f"Import failed: {exc}"


if __name__ == "__main__":
    mcp_app.run(transport="stdio")
