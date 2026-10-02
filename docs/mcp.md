# MATECOS MCP server

MATECOS exposes its existing tools over the Model Context Protocol (MCP). This is a protocol adapter; it reuses the current tool discovery, manifest, registry, and executor components.

## Current MATECOS architecture

- `src/tools/manifests.py` defines the internal `ToolManifest`, including JSON input/output schemas, runtime, risk, and provenance.
- `src/tools/registry.py` stores registered manifests and now emits registration/removal events for protocol adapters.
- `src/tools/executor.py` validates execution and returns a `ToolExecutionResult` with a status, output, and error.
- `src/api/routes/github_tools.py` discovers repository scripts and adds manifests and handlers to the shared registry/executor.
- `src/tools/mcp_adapter.py` maps the live registry into MCP tools and routes calls through the existing executor.

```text
MCP client -> official MCP SDK (Streamable HTTP or stdio)
           -> MCPToolAdapter -> ToolRegistry -> ToolExecutor -> existing handler/runtime
                                      ^
                                      +-- GitHub importer registers tools at runtime
```

The adapter has no GitHub parsing or discovery code. It receives a registry and executor, lists current enabled registry records, maps each manifest's `tool_id`, name, description, and JSON Schema to an MCP tool, validates call arguments, then delegates execution to `ToolExecutor`.

## Transport and run commands

The main MATECOS FastAPI app mounts Streamable HTTP at `/mcp`. With the default API port, connect to:

```text
http://127.0.0.1:8000/mcp
```

Start MATECOS as usual:

```powershell
.\.venv\Scripts\python.exe scripts\run_server.py
```

The standalone launcher also supports stdio (for desktop clients) and standalone Streamable HTTP:

```powershell
$env:MCP_TRANSPORT = "stdio"
.\.venv\Scripts\python.exe scripts\run_mcp_server.py
```

```powershell
$env:MCP_TRANSPORT = "streamable-http"
.\.venv\Scripts\python.exe scripts\run_mcp_server.py
```

The standalone HTTP server listens at `MCP_HOST:MCP_PORT` and `MCP_PATH` (defaults: `127.0.0.1:8765/mcp`). Use the mounted endpoint for a live view of repositories imported through the running MATECOS UI/API. A separate stdio process has its own in-memory registry; set `MCP_IMPORT_REPOSITORIES` to preload repositories when it starts.

## Example MCP client

With the main API server running, this lists tools and calls the calculator:

```powershell
.\.venv\Scripts\python.exe scripts\mcp_example_client.py http://127.0.0.1:8000/mcp
```

Choose another available tool and provide its JSON arguments with `--tool` and `--arguments`:

```powershell
.\.venv\Scripts\python.exe scripts\mcp_example_client.py http://127.0.0.1:8000/mcp --tool text.word.count --arguments '{"text":"count these words"}'
```

## Claude Desktop and Cursor

Both can launch the stdio server using an MCP server entry. Replace `C:\\path\\to\\matecos` with the checkout path on your machine. Add this under `mcpServers` in the client's MCP configuration:

```json
{
  "matecos": {
    "command": "C:\\path\\to\\matecos\\.venv\\Scripts\\python.exe",
    "args": ["C:\\path\\to\\matecos\\scripts\\run_mcp_server.py"],
    "env": {
      "MCP_TRANSPORT": "stdio",
      "PYTHONIOENCODING": "utf-8"
    }
  }
}
```

For a stdio process to expose imported repositories, add `MCP_IMPORT_REPOSITORIES` to `env`, for example:

```json
"MCP_IMPORT_REPOSITORIES": "bottlepy/bottle,sherlock-project/sherlock"
```

For clients that support a remote MCP URL, use `http://127.0.0.1:8000/mcp` while the main MATECOS server is running.

## Dynamic tools

`tools/list` reads the registry every time, so new imports and removals are reflected without editing the MCP server source. Registry changes publish the SDK's standard tool-list change event through its subscription handler. Existing tools remain available through the normal discovery/import flow.

The standalone stdio server is its own process and cannot see an import made later in a separate API process. Use the mounted Streamable HTTP endpoint for that shared live registry, or set `MCP_IMPORT_REPOSITORIES` so stdio imports repositories when it starts.

## Errors and validation

- Unknown or disabled tool: MCP tool result with `isError: true`.
- Invalid input: the manifest's JSON Schema is checked before dispatch; the first argument error is returned to the client.
- Timeout, blocked tool, missing Docker/runtime dependency, or execution failure: the executor's status/error is converted to an MCP tool error, with server-side logs retaining details.
- Success: MCP text content plus `structuredContent` containing the executor's output object.

## Security boundary for discovered code

Built-in tools have no external repository provenance. Tools imported from GitHub carry `source_repo` provenance and are blocked by `ToolExecutor` unless explicitly enabled. This gate applies to MCP calls, the direct tool API, and the task/orchestration paths that reuse the executor. By default, `ALLOW_UNTRUSTED_TOOLS=false`.

Review imported code before opting into execution:

```env
ALLOW_UNTRUSTED_TOOLS=true
```

Set this only for repositories you trust. The current GitHub script importer has a Python subprocess handler for some discovered scripts; that handler is not a security sandbox. The default executor gate prevents repository-derived code from running until an operator makes that trust decision. Container-backed execution still uses MATECOS's configured Docker sandbox.

MCP listens only on local hostnames by default (`MCP_HOST=127.0.0.1`). For remote deployment, configure the allowed host/security boundary and authentication at the ASGI deployment layer before exposing the endpoint publicly.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_ENABLED` | `true` | Mount MCP in the main FastAPI app. |
| `MCP_TRANSPORT` | `streamable-http` | Main-app mode or standalone launcher mode: `streamable-http` or `stdio`. |
| `MCP_SERVER_NAME` | `MATECOS` | MCP server identity reported to clients. |
| `MCP_PATH` | `/mcp` | MCP endpoint path. |
| `MCP_HOST` | `127.0.0.1` | Host allowlist/bind target for the standalone MCP server. |
| `MCP_PORT` | `8765` | Standalone Streamable HTTP port. The mounted app uses `API_PORT`. |
| `MCP_IMPORT_REPOSITORIES` | empty | Comma-separated repositories preloaded by the standalone launcher. |
| `ALLOW_UNTRUSTED_TOOLS` | `false` | Permit tools from imported repositories to execute. Review code first. |

Install dependencies from the lock file with `uv sync` or `pip install -e .`. The project uses the official MCP Python SDK (`mcp`); Streamable HTTP is the network transport and stdio is available for desktop clients.
