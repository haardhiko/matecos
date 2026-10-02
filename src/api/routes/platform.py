"""Platform level API features for MATECOS."""

import asyncio
import time
import platform
import sys
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.api.routes.github_tools import get_registry, get_executor
from src.api.routes.tasks import extract_payload, detect_capabilities
from src.tools.registry import ToolExecutionContext, ToolSearchQuery

router = APIRouter(prefix="/v1/platform", tags=["Platform"])

class BatchTask(BaseModel):
    id: str
    task: str = ""
    tool_id: str | None = None
    payload: dict | None = None

class BatchRequest(BaseModel):
    tasks: list[BatchTask]
    parallel: bool = True

class SnippetRequest(BaseModel):
    tool_id: str
    language: str = 'python'
    payload_example: dict | None = None

@router.post("/batch")
async def execute_batch(request: BatchRequest) -> dict:
    if len(request.tasks) > 10:
        raise HTTPException(status_code=400, detail="Batch size limit is 10.")

    registry = get_registry()
    executor = get_executor()
    
    async def process_task(batch_task: BatchTask) -> dict:
        start_time = time.time()
        context = ToolExecutionContext(
            execution_id=f"batch-{int(time.time() * 1000)}-{batch_task.id}",
            agent_id="batch-runner",
            user_id="dashboard-user",
        )
        
        try:
            if batch_task.tool_id:
                tool_id = batch_task.tool_id
                tool_record = registry.get(tool_id)
                if not tool_record:
                    return {"id": batch_task.id, "status": "FAILED", "error": f"Tool '{tool_id}' not found.", "duration_ms": 0}
                
                payload = batch_task.payload if batch_task.payload is not None else extract_payload(tool_id, batch_task.task)
                
                result = await executor.execute(tool_record.manifest, payload, context)
                registry.record_invocation(tool_id, success=(result.status == "SUCCEEDED"), latency_ms=float(result.duration_ms))
                
                return {
                    "id": batch_task.id,
                    "tool_selected": tool_id,
                    "status": result.status,
                    "result": result.output,
                    "error": result.error,
                    "duration_ms": result.duration_ms
                }
            else:
                # Auto selection
                required_caps = detect_capabilities(batch_task.task)
                all_tools = registry.list_all()
                for tool in all_tools:
                    tid_lower = tool.tool_id.lower().replace(".", " ")
                    name_lower = tool.manifest.name.lower()
                    if any(word in batch_task.task.lower() for word in tid_lower.split()):
                        required_caps.extend(tool.manifest.capabilities)
                    if any(word in batch_task.task.lower() for word in name_lower.split() if len(word) > 2):
                        required_caps.extend(tool.manifest.capabilities)

                required_caps = list(set(required_caps))
                candidates = await registry.search(ToolSearchQuery(capabilities=required_caps))

                if not candidates:
                    task_words = {w for w in batch_task.task.lower().split() if len(w) > 3}
                    for tool in all_tools:
                        desc_lower = tool.manifest.description.lower()
                        if any(word in desc_lower for word in task_words):
                            candidates.append(tool)

                if not candidates:
                    return {"id": batch_task.id, "status": "FAILED", "error": "No matching tool found.", "duration_ms": 0}

                def score(c):
                    s = 0.0
                    c_caps = set(c.manifest.capabilities)
                    req_set = set(required_caps)
                    if req_set:
                        intersection = len(c_caps & req_set)
                        union = len(c_caps | req_set)
                        s += (intersection / max(union, 1)) * 10
                    if c.manifest.runtime.type == "builtin":
                        s += 1
                    return s

                best = max(candidates, key=score)
                payload = extract_payload(best.tool_id, batch_task.task)
                result = await executor.execute(best.manifest, payload, context)
                registry.record_invocation(best.tool_id, success=(result.status == "SUCCEEDED"), latency_ms=float(result.duration_ms))
                
                return {
                    "id": batch_task.id,
                    "tool_selected": best.tool_id,
                    "status": result.status,
                    "result": result.output,
                    "error": result.error,
                    "duration_ms": result.duration_ms
                }

        except Exception as exc:
            return {
                "id": batch_task.id,
                "status": "FAILED",
                "error": str(exc),
                "duration_ms": int((time.time() - start_time) * 1000)
            }

    batch_start = time.time()
    
    if request.parallel:
        results = await asyncio.gather(*(process_task(t) for t in request.tasks))
    else:
        results = [await process_task(t) for t in request.tasks]
        
    succeeded = sum(1 for r in results if r.get("status") == "SUCCEEDED")
    failed = len(results) - succeeded
    
    return {
        "batch_id": f"batch-{int(time.time() * 1000)}",
        "results": list(results),
        "total_duration_ms": int((time.time() - batch_start) * 1000),
        "succeeded": succeeded,
        "failed": failed
    }

@router.get("/stats")
async def get_stats(req: Request) -> dict:
    registry = get_registry()
    tools = registry.list_all()
    
    total_tools = len(tools)
    total_executions = sum(t.invocation_count for t in tools)
    total_failures = sum(t.failure_count for t in tools)
    
    success_rate = 0.0
    if total_executions > 0:
        success_rate = ((total_executions - total_failures) / total_executions) * 100
        
    avg_latency = 0.0
    if total_executions > 0:
        avg_latency = sum(t.avg_latency_ms * t.invocation_count for t in tools) / total_executions
        
    tools_by_category = defaultdict(int)
    tools_by_risk = defaultdict(int)
    
    most_used = []
    
    for t in tools:
        cat = t.tool_id.split('.')[0]
        tools_by_category[cat] += 1
        tools_by_risk[t.manifest.risk_level] += 1
        
        tsr = 0.0
        if t.invocation_count > 0:
            tsr = ((t.invocation_count - t.failure_count) / t.invocation_count) * 100
            
        most_used.append({
            "tool_id": t.tool_id,
            "invocation_count": t.invocation_count,
            "success_rate": tsr
        })
        
    most_used.sort(key=lambda x: x["invocation_count"], reverse=True)
    
    uptime = 0.0
    if hasattr(req.app.state, "startup_time"):
        uptime = time.time() - req.app.state.startup_time
        
    return {
        "total_tools": total_tools,
        "total_executions": total_executions,
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency,
        "tools_by_category": dict(tools_by_category),
        "tools_by_risk": dict(tools_by_risk),
        "most_used_tools": most_used[:5],
        "recent_executions": 0, # Placeholder
        "uptime_seconds": uptime,
    }

@router.get("/api-info")
async def get_api_info() -> dict:
    return {
        "name": "MATECOS API",
        "version": "1.0.0",
        "description": "Multi-Agent Tool Ecosystem - API Platform",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "endpoints": [
            {"method": "POST", "path": "/v1/tasks/run", "description": "Run a task with auto tool selection"},
            {"method": "POST", "path": "/v1/tasks/run/{tool_id}", "description": "Run a task with a specific tool"},
            {"method": "POST", "path": "/v1/platform/batch", "description": "Batch execute multiple tasks"},
            {"method": "GET", "path": "/v1/github/tools", "description": "List all available tools"}
        ],
        "authentication": {
            "type": "Bearer Token",
            "header": "Authorization",
            "example": "Bearer your-api-key"
        },
        "rate_limits": {"requests_per_minute": 60, "batch_size": 10},
        "code_examples": {
            "python": 'import requests\n\nresp = requests.post("http://localhost:8000/v1/tasks/run", json={"task": "calculate 5 * 10"})\nprint(resp.json())',
            "javascript": 'fetch("http://localhost:8000/v1/tasks/run", {\n  method: "POST",\n  headers: {"Content-Type": "application/json"},\n  body: JSON.stringify({task: "calculate 5 * 10"})\n}).then(r => r.json()).then(console.log);',
            "curl": 'curl -X POST http://localhost:8000/v1/tasks/run -H "Content-Type: application/json" -d \'{"task": "calculate 5 * 10"}\''
        }
    }

@router.post("/snippet")
async def generate_snippet(request: SnippetRequest) -> dict:
    import json
    
    registry = get_registry()
    tool_record = registry.get(request.tool_id)
    if not tool_record:
        raise HTTPException(status_code=404, detail="Tool not found")
        
    payload = request.payload_example or {"example_key": "example_value"}
    json_payload = json.dumps({"payload": payload})
    
    code = ""
    if request.language == "python":
        code = f'''import requests

url = "http://localhost:8000/v1/github/tools/{request.tool_id}/execute"
payload = {json_payload}

response = requests.post(url, json=payload)
print(response.json())
'''
    elif request.language == "javascript":
        code = f'''fetch("http://localhost:8000/v1/github/tools/{request.tool_id}/execute", {{
  method: "POST",
  headers: {{"Content-Type": "application/json"}},
  body: JSON.stringify({json_payload})
}})
.then(response => response.json())
.then(data => console.log(data));
'''
    elif request.language == "curl":
        code = f'''curl -X POST "http://localhost:8000/v1/github/tools/{request.tool_id}/execute" \\
     -H "Content-Type: application/json" \\
     -d '{json_payload}'
'''
    else:
        raise HTTPException(status_code=400, detail="Unsupported language")
        
    return {
        "language": request.language,
        "code": code,
        "tool_id": request.tool_id
    }

@router.get("/categories")
async def get_categories() -> dict:
    registry = get_registry()
    tools = registry.list_all()
    
    categories_map = defaultdict(list)
    for t in tools:
        cat = t.tool_id.split('.')[0]
        categories_map[cat].append({
            "tool_id": t.tool_id,
            "name": t.manifest.name,
            "description": t.manifest.description
        })
        
    categories = []
    for cat, ts in categories_map.items():
        categories.append({
            "name": cat.capitalize() + " Tools",
            "id": cat,
            "tool_count": len(ts),
            "tools": ts
        })
        
    return {"categories": categories}

@router.get("/health")
async def platform_health(req: Request) -> dict:
    registry = get_registry()
    tools = registry.list_all()
    
    uptime = 0.0
    if hasattr(req.app.state, "startup_time"):
        uptime = time.time() - req.app.state.startup_time
        
    healthy_tools = sum(1 for t in tools if t.health_status == "healthy" or t.health_status == "unknown")
    
    return {
        "status": "healthy",
        "tools_registered": len(tools),
        "tools_healthy": healthy_tools,
        "executor_status": "ready",
        "uptime_seconds": uptime,
        "version": "1.0.0",
        "python_version": sys.version.split()[0],
        "platform": platform.platform()
    }


class LLMUpdatePayload(BaseModel):
    provider: str
    api_key: str = ""
    model: str = "gemini-1.5-flash"
    base_url: str = ""
    enabled: bool = True
    temperature: float = 0.3


@router.get("/llm")
async def get_llm_status() -> dict:
    """Get current LLM configuration and connectivity status."""
    from src.services.llm_service import llm_service, LLMConfigModel
    cfg = llm_service.config
    masked_key = ""
    if cfg.api_key:
        masked_key = cfg.api_key[:4] + "..." + cfg.api_key[-4:] if len(cfg.api_key) > 8 else "***"
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "enabled": cfg.enabled,
        "has_api_key": bool(cfg.api_key),
        "masked_key": masked_key,
        "temperature": cfg.temperature,
    }


@router.post("/llm")
async def update_llm_config(payload: LLMUpdatePayload) -> dict:
    """Update and save LLM configuration."""
    from src.services.llm_service import llm_service, LLMConfigModel
    # An empty key means the user left the password field unchanged.
    api_key = payload.api_key.strip() or llm_service.config.api_key
    new_cfg = LLMConfigModel(
        provider=payload.provider.lower(),
        api_key=api_key,
        model=payload.model.strip(),
        base_url=payload.base_url.strip(),
        enabled=payload.enabled,
        temperature=payload.temperature,
    )
    llm_service.save_config(new_cfg)
    return {"status": "saved", "provider": new_cfg.provider, "model": new_cfg.model, "enabled": new_cfg.enabled}


@router.post("/llm/test")
async def test_llm_connection() -> dict:
    """Send a small test prompt to verify LLM connection."""
    from src.services.llm_service import llm_service
    if not llm_service.config.enabled or not llm_service.config.api_key:
        raise HTTPException(status_code=400, detail="LLM is not enabled or missing API key.")
    try:
        reply = await llm_service.call_llm("Respond with exactly the word: 'Connected'")
        return {"status": "success", "response": reply.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM connection test failed: {str(e)}")
