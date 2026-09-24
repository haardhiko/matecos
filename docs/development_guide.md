# MATECOS Development Guide

## 1. Quick Start

### 1.1 Prerequisites
- Python 3.12+ (managed via `uv` or standard Python)
- Docker Desktop (for sandboxed container execution)
- Redis 7+ and PostgreSQL 16+ (optional for mock/development mode)

### 1.2 Setup Environment
```powershell
# Clone or navigate to the project directory
cd C:\Users\haard\.gemini\antigravity\scratch\matecos

# Create virtual environment and install dependencies
uv venv --python 3.12
.venv\Scripts\activate
uv pip install -e ".[dev]"
```

### 1.3 Configuration
Copy the sample environment file:
```powershell
copy .env.example .env
```
Key configuration settings:
- `APP_ENV`: Set to `development` for in-memory mocks and authentication bypass, or `production` for strict enforcement.
- `LLM_PROVIDER`: `mock`, `openai`, `anthropic`, `gemini`, or `ollama`.
- `RISK_POLICY_PROFILE`: `conservative`, `balanced`, or `permissive`.

---

## 2. Running the Application

### 2.1 Start the API Server
```powershell
python scripts/run_server.py
```
The server will start at `http://localhost:8000`.
- Swagger UI: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Readiness check: `http://localhost:8000/readiness`
- Metrics: `http://localhost:8000/metrics`

### 2.2 Seed Builtin Tools
```powershell
python scripts/seed_tools.py
```
Registers builtin tools:
- `math.calculator` (Builtin safe arithmetic)
- `data.csv.profile` (Tabular statistics profiling)
- `web.http_fetch` (Guarded web retrieval)

### 2.3 Run the End-to-End Demo CLI
```powershell
python scripts/demo_run.py
```
Executes a simulated multi-agent workflow:
1. Decomposes natural language goal into a 2-step DAG.
2. Spawns an `analyst` agent and a `writer` agent.
3. Performs non-bypassable pre/post risk checks.
4. Produces an aggregated, verified executive report.

---

## 3. Testing Strategy

### 3.1 Running Tests
```powershell
# Run all unit tests
pytest tests/unit -v

# Run security test suite
pytest tests/security -v

# Run contract verification
pytest tests/contract -v

# Run end-to-end integration test
pytest tests/end_to_end -v

# Run full test suite with coverage
pytest --cov=src --cov-report=term-missing
```

### 3.2 Test Structure
- `tests/unit/test_state_machine.py`: State transition rules, locks, and invalid transition handling.
- `tests/unit/test_agents.py`: Agent roles, budget exhaustion, prompt rendering, and ReAct loop.
- `tests/unit/test_task_graph.py`: DAG validation, Kahn's cycle detection, topological sorting, and readiness.
- `tests/unit/test_risk_engine.py`: 6 deterministic risk classifiers and policy mappings.
- `tests/unit/test_registry.py`: Static tool registry, searches, and health metrics.
- `tests/unit/test_memory.py`: Event store, working memory with TTL, and episodic memory search.
- `tests/unit/test_orchestrator.py`: Orchestrator lifecycle and scheduler concurrency.
- `tests/unit/test_github_adapter.py`: Cloner, indexer, safe querying, and PR management.
- `tests/unit/test_verification.py`: Input validation, result checker, and data provenance.
- `tests/unit/test_api.py`: FastAPI endpoints (health, requests, cancellation).
- `tests/security/test_security.py`: RBAC permissions, secrets manager, and `--network none` isolation invariant.
- `tests/contract/test_contract.py`: RFC 9457 error contracts and ToolManifest schema validations.
- `tests/end_to_end/test_e2e.py`: Full multi-agent orchestration execution.

---

## 4. Docker Sandbox Setup
To execute tools requiring subprocess/container isolation:
```powershell
docker build -t matecos-sandbox:latest -f docker/Dockerfile.sandbox .
```
The sandbox image runs as non-root user `65534:65534` (`nobody`), drops all Linux capabilities, and executes strictly with `--network none`.
