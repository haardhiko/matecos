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
    ("math", "calculate", "compute", "arithmetic", "formula", "sin", "cos", "sqrt", "log", "+", "*", "/"):
        ["arithmetic", "math", "calculator"],
    ("fetch", "download", "http", "url", "web", "scrape", "api", "request", "get"):
        ["http", "web_fetch", "network"],
    ("csv", "data", "profile", "analyze", "columns", "statistics", "schema"):
        ["data_analysis", "csv", "profiling"],
    ("code", "python", "script", "program", "execute", "run"):
        ["python", "code_execution"],
    ("file", "read", "write", "filesystem"):
        ["filesystem"],
}


def _detect_capabilities(task_text: str) -> list[str]:
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


def _extract_payload(tool_id: str, task_text: str) -> dict:
    """Build the right payload for a tool based on the task text."""
    if tool_id == "math.calculator":
        # Extract math expression — strip non-math text
        # First try to find an explicit expression
        expr = task_text
        # Remove common prefixes
        for prefix in ["calculate", "compute", "what is", "what's", "evaluate", "solve"]:
            expr = re.sub(rf"^\s*{prefix}\s*", "", expr, flags=re.IGNORECASE)
        # Strip any remaining non-math characters at the edges
        expr = expr.strip().rstrip("?!.")
        return {"expression": expr}

    elif tool_id == "web.http_fetch":
        # Extract URL from the text
        match = re.search(r"(https?://[^\s\"'<>]+)", task_text)
        url = match.group(1).rstrip(".,;:!?)") if match else task_text.strip()
        return {"url": url, "method": "GET"}

    elif tool_id == "data.csv.profile":
        # Extract file URI — look for secure:// or .csv paths
        match = re.search(r"(secure://[^\s]+\.csv)", task_text)
        if not match:
            match = re.search(r"([a-zA-Z0-9_/\\.\-]+\.csv)", task_text)
        file_uri = match.group(1) if match else "secure://uploads/data.csv"
        return {"file_uri": file_uri}

    else:
        # For github-imported or other tools — pass as generic args
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
    required_caps = _detect_capabilities(task_text)

    # Step 2: Also match against tool IDs and names directly
    all_tools = registry.list_all()
    for tool in all_tools:
        tid_lower = tool.tool_id.lower().replace(".", " ")
        name_lower = tool.manifest.name.lower()
        if any(word in task_text.lower() for word in tid_lower.split()):
            required_caps.extend(tool.manifest.capabilities)
        if any(word in task_text.lower() for word in name_lower.split() if len(word) > 2):
            required_caps.extend(tool.manifest.capabilities)

    required_caps = list(set(required_caps))

    # Step 3: Search registry
    candidates = await registry.search(ToolSearchQuery(capabilities=required_caps))

    # Step 4: Fuzzy fallback — match on description text
    if not candidates:
        task_words = {w for w in task_text.lower().split() if len(w) > 3}
        for tool in all_tools:
            desc_lower = tool.manifest.description.lower()
            if any(word in desc_lower for word in task_words):
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
        # Bonus for description match
        if any(w in c.manifest.description.lower() for w in task_text.lower().split() if len(w) > 3):
            s += 2
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
    payload = _extract_payload(best.tool_id, request.task)
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
            "tool_name": best.manifest.name,
            "reasoning": reasoning,
            "result": None,
            "error": str(exc),
            "duration_ms": int((time.time() - start_time) * 1000),
            "status": "FAILED",
        }

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
