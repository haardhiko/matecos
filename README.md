# OrchaDeck

**OrchaDeck** (formerly MATECOS) is a production-grade multi-agent tool ecosystem and dynamic tool orchestration platform. It imports tools from arbitrary repositories, discovers usable entry points across multiple programming languages, generates strict tool manifests, registers them into an ecosystem, and exposes them directly to AI agents via FastAPI and the Model Context Protocol (MCP).

---

## Key Features

- **Universal Multi-Language Tool Importer:**
  - Automated detection and candidate discovery for **Python, Node.js/TypeScript, Go, Rust, Java, C/C++, Shell scripts, and Docker containers**.
  - Monorepo package scanning (`packages/*/package.json`, `packages/*/pyproject.toml`).
  - Isolated error handling: A failure in one candidate tool does not abort the entire repository import.

- **Deterministic Tool ID System:**
  - Centralized identifier generation matching `^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$`.
  - Guaranteed normalization against special characters, spaces, hyphens, empty segments, and collisions.

- **15 Builtin Tools:**
  - `math.calculator`, `web.http_fetch`, `data.csv.profile`, `text.json.format`, `text.base64.codec`, `crypto.hash.gen`, `util.uuid.gen`, `text.regex.test`, `util.timestamp.convert`, `text.word.count`, `text.url.parse`, `util.password.gen`, `text.diff.compare`, `text.encode.convert`, and `data.convert.units`.

- **Model Context Protocol (MCP) Server:**
  - Seamless integration with **OpenCode**, Claude Desktop, and any standard MCP client over stdio.
  - Exposes `orchadeck_execute_tool`, `orchadeck_list_tools`, `orchadeck_import_github_repo`, plus direct tool bindings.

- **Control-Plane Web Dashboard:**
  - Full single-page management dashboard at `http://127.0.0.1:8000/`.
  - Conversational Agent Shell, real-time tool execution traces, batch runner, and GitHub repository importer UI.

---

## Quickstart

### 1. Installation

```powershell
# Create & activate virtual environment
uv venv
.venv\Scripts\activate

# Install dependencies
uv sync
```

### 2. Run the Platform Server

```powershell
python scripts/run_server.py
```

The web dashboard is now accessible at [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

### 3. Run the MCP Server for OpenCode

OrchaDeck includes an MCP server configured in `mcp_server.py`.

In `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "orchadeck": {
      "type": "local",
      "command": [
        "C:\\Users\\haard\\.gemini\\antigravity\\scratch\\matecos\\.venv\\Scripts\\python.exe",
        "C:\\Users\\haard\\.gemini\\antigravity\\scratch\\matecos\\mcp_server.py"
      ]
    }
  }
}
```

Verify in OpenCode:
```powershell
opencode mcp list
```

---

## Running Automated Tests

```powershell
# Run full unit test suite (266+ tests)
python -m pytest tests/unit/ -v

# Run real repository import integration test
python -m pytest tests/end_to_end/test_import_e2e.py -v
```
