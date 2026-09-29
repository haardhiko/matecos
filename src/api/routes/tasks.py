"""Smart task execution — user gives natural language, system picks the right tool."""

from __future__ import annotations

import re
import time
from datetime import datetime, UTC
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.routes.github_tools import get_executor, get_registry
from src.tools.registry import ToolExecutionContext, ToolSearchQuery

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/tasks", tags=["Task Execution"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RunTaskRequest(BaseModel):
    task: str
    max_cost: float = 10.0


# In-memory history (last 50)
_task_history: list[dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Keyword → capability mapping
# ---------------------------------------------------------------------------

_CAPABILITY_MAP: dict[tuple[str, ...], list[str]] = {
    # Original 3 tools
    ("math", "calculate", "compute", "arithmetic", "formula", "sin", "cos", "sqrt", "log"):
        ["arithmetic", "math", "calculator"],
    ("fetch", "download", "http", "scrape", "api", "request"):
        ["http", "web_fetch", "network"],
    ("csv", "profile", "columns", "statistics", "schema"):
        ["data_analysis", "csv", "profiling"],
    # New tools
    ("json", "format json", "validate json", "parse json", "minify json", "pretty print"):
        ["json", "format", "validate", "minify"],
    ("base64",):
        ["base64", "encode", "decode", "encoding"],
    ("hash", "sha256", "sha512", "md5", "checksum", "sha1"):
        ["hash", "sha256", "md5", "checksum", "crypto"],
    ("uuid", "unique id", "guid"):
        ["uuid", "generate", "identifier", "unique"],
    ("regex", "regular expression", "pattern match"):
        ["regex", "pattern", "match", "search", "text_processing"],
    ("timestamp", "epoch", "unix time", "date convert", "datetime"):
        ["timestamp", "datetime", "convert", "timezone", "epoch"],
    ("word count", "character count", "reading time", "count words", "count the words"):
        ["word_count", "character_count", "text_analysis", "statistics"],
    ("parse url", "url parse", "query string", "url components"):
        ["url", "parse", "query_string", "uri"],
    ("password", "passphrase", "generate password", "secure password"):
        ["password", "generate", "security", "random"],
    ("diff", "compare text", "difference", "text comparison"):
        ["diff", "compare", "text_comparison", "changes"],
    ("url encode", "html encode", "hex encode", "rot13", "encode text", "decode text"):
        ["encode", "decode", "url_encode", "html_encode", "hex"],
    ("convert", "celsius", "fahrenheit", "kelvin", "miles", "kilometers", "pounds", "kilograms", "units"):
        ["convert", "units", "measurement", "temperature", "length", "weight"],
    # Generic
    ("code", "python", "script", "program"):
        ["python", "code_execution"],
}


def detect_capabilities(task_text: str) -> list[str]:
    """Map task text keywords to tool capabilities."""
    task_lower = task_text.lower()
    caps: set[str] = set()

    for keywords, mapped_caps in _CAPABILITY_MAP.items():
        if any(kw in task_lower for kw in keywords):
            caps.update(mapped_caps)

    # Also detect math expressions directly (numbers + operators)
    if re.search(r"\d+\s*[+\-*/^]\s*\d+", task_text):
        caps.update(["arithmetic", "math", "calculator"])

    # Detect URLs
    if re.search(r"https?://", task_text):
        caps.update(["http", "web_fetch", "network"])

    return list(caps)


def extract_payload(tool_id: str, task_text: str) -> dict:
    """Build the right payload for a tool based on the task text."""
    if tool_id == "math.calculator":
        expr = task_text
        for prefix in ["calculate", "compute", "what is", "what's", "evaluate", "solve"]:
            expr = re.sub(rf"^\s*{prefix}\s*", "", expr, flags=re.IGNORECASE)
        expr = expr.strip().rstrip("?!.")
        return {"expression": expr}

    elif tool_id == "web.http_fetch":
        match = re.search(r"(https?://[^\s\"'<>]+)", task_text)
        url = match.group(1).rstrip(".,;:!?)") if match else task_text.strip()
        return {"url": url, "method": "GET"}

    elif tool_id == "data.csv.profile":
        match = re.search(r"(secure://[^\s]+\.csv)", task_text)
        if not match:
            match = re.search(r"([a-zA-Z0-9_/\\\.\-]+\.csv)", task_text)
        file_uri = match.group(1) if match else "secure://uploads/data.csv"
        return {"file_uri": file_uri}

    elif tool_id == "text.json.format":
        # Extract JSON from the task text
        match = re.search(r'(\{[^{}]*\}|\[[^\[\]]*\])', task_text)
        text = match.group(1) if match else task_text
        return {"text": text, "indent": 2, "sort_keys": False, "minify": "minify" in task_text.lower()}

    elif tool_id == "text.base64.codec":
        action = "decode" if "decode" in task_text.lower() else "encode"
        # Strip the command prefix to get the actual text
        text = task_text
        for prefix in ["base64 encode", "base64 decode", "encode", "decode"]:
            text = re.sub(rf"^\s*{prefix}\s*", "", text, flags=re.IGNORECASE)
        return {"text": text.strip(), "action": action}

    elif tool_id == "crypto.hash.gen":
        algo = "sha256"
        for a in ["md5", "sha1", "sha512", "sha256"]:
            if a in task_text.lower():
                algo = a
                break
        text = task_text
        for prefix in ["hash", "generate hash", "hash the text", f"using {algo}"]:
            text = re.sub(rf"\s*{prefix}\s*", " ", text, flags=re.IGNORECASE)
        return {"text": text.strip(), "algorithm": algo}

    elif tool_id == "util.uuid.gen":
        match = re.search(r"(\d+)", task_text)
        count = min(int(match.group(1)), 100) if match else 1
        return {"count": count, "version": 4, "uppercase": False}

    elif tool_id == "text.regex.test":
        # Try to extract pattern and text
        parts = re.split(r"\s+(?:against|on|in|with)\s+", task_text, maxsplit=1, flags=re.IGNORECASE)
        pattern = parts[0].strip() if parts else task_text
        text = parts[1].strip() if len(parts) > 1 else ""
        return {"pattern": pattern, "text": text}

    elif tool_id == "util.timestamp.convert":
        # Extract a number (epoch) or date string
        match = re.search(r"(\d{10,13})", task_text)
        if match:
            return {"value": match.group(1), "from_format": "auto", "to_format": "iso8601"}
        return {"value": "now", "from_format": "auto", "to_format": "iso8601"}

    elif tool_id == "text.word.count":
        text = task_text
        for prefix in ["count the words in:", "count words in:", "word count:", "count words", "count the words"]:
            text = re.sub(rf"^\s*{prefix}\s*", "", text, flags=re.IGNORECASE)
        return {"text": text.strip()}

    elif tool_id == "text.url.parse":
        match = re.search(r"(https?://[^\s\"'<>]+)", task_text)
        url = match.group(1) if match else task_text.strip()
        return {"url": url}

    elif tool_id == "util.password.gen":
        match = re.search(r"(\d+)", task_text)
        length = min(int(match.group(1)), 128) if match else 16
        count_match = re.search(r"(\d+)\s*password", task_text)
        count = min(int(count_match.group(1)), 20) if count_match else 1
        return {"length": length, "count": count, "uppercase": True, "lowercase": True,
                "digits": True, "symbols": True, "exclude": ""}

    elif tool_id == "text.diff.compare":
        # Split on "and" or "vs" or newlines
        parts = re.split(r"\s+(?:and|vs|versus|compared to)\s+", task_text, maxsplit=1, flags=re.IGNORECASE)
        text_a = parts[0].strip() if parts else ""
        text_b = parts[1].strip() if len(parts) > 1 else ""
        return {"text_a": text_a, "text_b": text_b, "context_lines": 3}

    elif tool_id == "text.encode.convert":
        action = "decode" if "decode" in task_text.lower() else "encode"
        encoding = "url"
        for enc in ["html", "hex", "rot13", "ascii85"]:
            if enc in task_text.lower():
                encoding = enc
                break
        text = task_text
        for prefix in [f"{encoding} encode", f"{encoding} decode", "encode", "decode"]:
            text = re.sub(rf"^\s*{prefix}\s*", "", text, flags=re.IGNORECASE)
        return {"text": text.strip(), "action": action, "encoding": encoding}

    elif tool_id == "data.convert.units":
        # Try to extract: <value> <from_unit> to <to_unit>
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(\w+)\s+(?:to|in|->)\s+(\w+)",
            task_text, re.IGNORECASE
        )
        if match:
            return {"value": float(match.group(1)), "from_unit": match.group(2).lower(),
                    "to_unit": match.group(3).lower()}
        return {"value": 0, "from_unit": "unknown", "to_unit": "unknown"}

    else:
        return {"args": [task_text], "stdin": task_text}



# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run")
async def run_task(request: RunTaskRequest) -> dict:
    """Run a natural language task — auto-selects the best tool and executes it."""
    registry = get_registry()
    executor = get_executor()

    task_text = request.task.strip()
    if not task_text:
        raise HTTPException(status_code=400, detail="Task text cannot be empty.")

    # Step 1: Detect capabilities from the task text
    required_caps = detect_capabilities(task_text)
    task_tokens = set(re.findall(r"[a-z0-9_]+", task_text.lower()))

    # Direct intent boosts for specific tools
    direct_tool_intents: dict[str, list[str]] = {
        "util.uuid.gen": ["uuid", "uuids", "guid", "guids"],
        "util.password.gen": ["password", "passwords", "passphrase"],
        "text.base64.codec": ["base64"],
        "crypto.hash.gen": ["hash", "sha256", "sha512", "md5", "sha1", "checksum"],
        "text.json.format": ["json"],
        "text.regex.test": ["regex", "regexp"],
        "util.timestamp.convert": ["timestamp", "epoch"],
        "text.word.count": ["words", "word count", "character count"],
        "text.url.parse": ["parse url", "url parse", "query string"],
        "text.diff.compare": ["diff", "compare"],
        "text.encode.convert": ["rot13", "html encode", "url encode", "hex encode"],
        "data.convert.units": ["convert", "celsius", "fahrenheit", "kelvin", "miles", "km", "units"],
        "math.calculator": ["calculate", "calculator", "compute", "math", "sqrt", "sin", "cos"],
        "web.http_fetch": ["fetch", "http", "download"],
        "data.csv.profile": ["csv", "profile"],
    }

    # Step 2: Match against tool IDs and names using whole-token matching
    ignore_tokens = {"gen", "tool", "util", "test", "data", "text", "the", "a", "an", "in", "to", "for", "of"}
    all_tools = registry.list_all()
    for tool in all_tools:
        tid_tokens = set(tool.tool_id.lower().replace(".", " ").split()) - ignore_tokens
        name_tokens = set(tool.manifest.name.lower().split()) - ignore_tokens
        if tid_tokens & task_tokens:
            required_caps.extend(tool.manifest.capabilities)
        if name_tokens & task_tokens:
            required_caps.extend(tool.manifest.capabilities)

    required_caps = list(set(required_caps))

    # Step 3: Search registry
    candidates = await registry.search(ToolSearchQuery(capabilities=required_caps))

    # Step 4: Fallback — if no candidates found or if direct intent matches, include direct intent tools
    for tool in all_tools:
        intents = direct_tool_intents.get(tool.tool_id, [])
        if any(intent in task_text.lower() for intent in intents):
            if tool not in candidates:
                candidates.append(tool)

    if not candidates:
        task_words = {w for w in task_tokens if len(w) > 3}
        for tool in all_tools:
            desc_words = set(re.findall(r"[a-z0-9_]+", tool.manifest.description.lower()))
            if task_words & desc_words:
                candidates.append(tool)

    # Step 5: If still nothing, return available tools
    if not candidates:
        return {
            "task": request.task,
            "tool_selected": None,
            "tool_name": None,
            "reasoning": "No matching tool found for this task.",
            "result": None,
            "duration_ms": 0,
            "status": "NO_TOOL_FOUND",
            "available_tools": [
                {"tool_id": t.tool_id, "name": t.manifest.name, "capabilities": t.manifest.capabilities}
                for t in all_tools
            ],
        }

    # Step 6: Rank candidates
    def score(c):
        s = 0.0
        c_caps = set(c.manifest.capabilities)
        req_set = set(required_caps)
        if req_set:
            intersection = len(c_caps & req_set)
            union = len(c_caps | req_set)
            s += (intersection / max(union, 1)) * 10

        # Massive boost for direct intent match
        intents = direct_tool_intents.get(c.tool_id, [])
        for intent in intents:
            if " " in intent:
                if intent in task_text.lower():
                    s += 40
            elif intent in task_tokens:
                s += 40

        # Bonus for description word overlap
        desc_words = set(re.findall(r"[a-z0-9_]+", c.manifest.description.lower()))
        s += len(task_tokens & desc_words) * 1.5

        # Penalize if over budget
        if c.manifest.limits.cost_estimate_usd > request.max_cost:
            s -= 100
        # Prefer builtins (more reliable)
        if c.manifest.runtime.type == "builtin":
            s += 1
        return s

    best = max(candidates, key=score)

    if score(best) < -50:
        return {
            "task": request.task,
            "tool_selected": best.tool_id,
            "tool_name": best.manifest.name,
            "reasoning": "Matching tools exceed your max_cost budget.",
            "result": None,
            "duration_ms": 0,
            "status": "OVER_BUDGET",
        }

    # Step 7: Extract payload and execute
    payload = extract_payload(best.tool_id, request.task)
    reasoning = (
        f"Selected '{best.manifest.name}' based on capability match: "
        f"{best.manifest.capabilities}. "
        f"Detected keywords mapped to: {required_caps[:5]}"
    )

    context = ToolExecutionContext(
        execution_id=f"task-{int(time.time() * 1000)}",
        agent_id="task-runner",
        user_id="dashboard-user",
    )

    start_time = time.time()
    try:
        result = await executor.execute(best.manifest, payload, context)
        exec_result = {
            "task": request.task,
            "tool_selected": best.tool_id,
            "tool_name": best.manifest.name,
            "reasoning": reasoning,
            "result": result.output,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "status": result.status,
        }
    except Exception as exc:
        logger.exception("task.execution_failed", tool_id=best.tool_id)
        exec_result = {
            "task": request.task,
            "tool_selected": best.tool_id,
            "result": None,
            "error": str(exc),
            "duration_ms": int((time.time() - start_time) * 1000),
            "status": "FAILED",
        }

    # Generate human-understandable explanation via LLM / smart synthesizer
    from src.services.llm_service import llm_service

    explanation = await llm_service.explain_result(
        task=request.task,
        tool_id=best.tool_id,
        tool_name=best.manifest.name,
        result=exec_result.get("result"),
        error=exec_result.get("error"),
    )
    exec_result["explanation"] = explanation

    # Record history
    exec_result["timestamp"] = datetime.now(UTC).isoformat()
    _task_history.append(exec_result)
    if len(_task_history) > 50:
        _task_history.pop(0)

    # Record metrics on the registry
    registry.record_invocation(
        best.tool_id,
        success=(exec_result["status"] == "SUCCEEDED"),
        latency_ms=float(exec_result["duration_ms"]),
    )

    return exec_result


@router.get("/capabilities")
async def list_capabilities() -> dict:
    """List all available capabilities grouped by tool."""
    registry = get_registry()
    return {
        t.tool_id: {
            "name": t.manifest.name,
            "capabilities": t.manifest.capabilities,
            "risk_level": t.manifest.risk_level,
        }
        for t in registry.list_all()
    }


@router.get("/history")
async def get_history() -> list[dict]:
    """Return the last 50 task executions."""
    return list(reversed(_task_history))


@router.post("/run/{tool_id}")
async def run_task_with_tool(tool_id: str, request: RunTaskRequest) -> dict:
    """Run a natural language task using a specific tool."""
    registry = get_registry()
    executor = get_executor()

    task_text = request.task.strip()
    if not task_text:
        raise HTTPException(status_code=400, detail="Task text cannot be empty.")

    tool_record = registry.get(tool_id)
    if not tool_record:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found.")

    payload = extract_payload(tool_id, task_text)
    
    context = ToolExecutionContext(
        execution_id=f"task-{int(time.time() * 1000)}",
        agent_id="task-runner",
        user_id="dashboard-user",
    )

    start_time = time.time()
    try:
        result = await executor.execute(tool_record.manifest, payload, context)
        exec_result = {
            "task": request.task,
            "tool_selected": tool_id,
            "tool_name": tool_record.manifest.name,
            "reasoning": f"Manually selected tool {tool_id}",
            "result": result.output,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "status": result.status,
        }
    except Exception as exc:
        logger.exception("task.execution_failed", tool_id=tool_id)
        exec_result = {
            "task": request.task,
            "tool_selected": tool_id,
            "result": None,
            "error": str(exc),
            "duration_ms": int((time.time() - start_time) * 1000),
            "status": "FAILED",
        }

    from src.services.llm_service import llm_service
    explanation = await llm_service.explain_result(
        task=request.task,
        tool_id=tool_id,
        tool_name=tool_record.manifest.name,
        result=exec_result.get("result"),
        error=exec_result.get("error"),
    )
    exec_result["explanation"] = explanation

    exec_result["timestamp"] = datetime.now(UTC).isoformat()
    _task_history.append(exec_result)
    if len(_task_history) > 50:
        _task_history.pop(0)

    registry.record_invocation(
        tool_id,
        success=(exec_result["status"] == "SUCCEEDED"),
        latency_ms=float(exec_result["duration_ms"]),
    )

    return exec_result
