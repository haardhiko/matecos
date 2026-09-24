# MATECOS Architecture Documentation

## 1. System Overview

**MATECOS** (Multi-Agent Tool Ecosystem) is a production-oriented multi-agent orchestration platform designed to accept high-level natural language requests, dynamically decompose them into a directed acyclic task graph (DAG), spawn specialized autonomous agents to execute tasks via secure tools, analyze risk before and after every action, preserve structured temporal memory, and return verified results.

```mermaid
flowchart TD
    User([User Request]) --> Gateway[Request Gateway / API]
    Gateway --> Planner[Goal Decomposition / Planner]
    Planner --> Graph[Task Graph DAG]
    Graph --> Scheduler[Task Scheduler]
    Scheduler --> AgentPool[Specialized Agents Pool]
    
    subgraph Agent Loop [ReAct Agent Runtime]
        Agent[Autonomous Agent] --> Prompt[Prompt Renderer]
        Prompt --> LLM[LLM Provider Interface]
        LLM --> Decision[Structured ActionDecision]
        Decision --> RiskEnginePre{Risk Engine Pre-Check}
        RiskEnginePre -- Denied --> Agent
        RiskEnginePre -- Requires Human Approval --> ApprovalQueue[Approval Queue]
        RiskEnginePre -- Allowed --> Executor[Tool Executor]
        Executor --> ToolRegistry[Tool Registry]
        Executor --> Sandbox[Docker Ephemeral Sandbox]
        Sandbox --> RiskEnginePost{Risk Engine Post-Check}
        RiskEnginePost --> Agent
    end

    AgentPool --> Agent Loop
    Agent Loop --> Verifier[Verification Layer]
    Verifier --> Memory[Temporal Memory System]
    Memory --> Result([Verified Final Result])
```

---

## 2. Core Subsystems

### 2.1 Request Gateway & Foundation (Phase 1)
- **API Framework**: FastAPI with Pydantic v2 schemas and RFC 9457 compliant error handling.
- **Identities**: Cryptographically random ULID strings used as primary identifiers for Executions, Tasks, Agents, and Invocations.
- **State Machines**: Three nested, strictly validated finite state machines:
  - `ExecutionStateMachine`: `CREATED` $\to$ `VALIDATING` $\to$ `PLANNING` $\to$ `AWAITING_APPROVAL` $\to$ `RUNNING` $\to$ `PARTIALLY_COMPLETED` / `COMPLETED` / `FAILED` / `CANCELLED` / `TIMED_OUT`.
  - `AgentStateMachine`: `CREATED` $\to$ `READY` $\to$ `RUNNING` $\to$ `WAITING_FOR_DEPENDENCY` $\to$ `WAITING_FOR_APPROVAL` $\to$ `RETRYING` $\to$ `COMPLETED` / `FAILED` / `CANCELLED`.
  - `ToolInvocationStateMachine`: `REQUESTED` $\to$ `RISK_CHECKING` $\to$ `APPROVED` $\to$ `RUNNING` $\to$ `SUCCEEDED` / `FAILED` / `TIMED_OUT` / `BLOCKED`.
- **Infrastructure**: Async SQLAlchemy (PostgreSQL + pgvector), Redis Streams for message passing and distributed locks, OpenTelemetry metrics and distributed tracing, structlog for JSON logs.

### 2.2 Static Tool Registry & Sandbox (Phase 2)
- **Manifests**: Strict Pydantic models validating semver, tool IDs, schemas, and resource limits.
- **Isolation Guarantee**: Sandboxed containers run with `--network none` as an unalterable security invariant, preventing exfiltration.
- **Selection Formula**: Weighted ranking score:
  $$\text{Score} = 0.40 \cdot \text{Capability} + 0.20 \cdot \text{Health} + 0.20 \cdot \text{Risk} + 0.10 \cdot \text{Latency} + 0.10 \cdot \text{Cost}$$

### 2.3 Single-Agent Runtime (Phase 3)
- **Provider-Neutral Interface**: `LLMProvider` Protocol decouples business logic from specific vendors (OpenAI, Anthropic, Gemini, Ollama, and Mock).
- **Prompt Security**: Strict prompt injection guards treating external outputs purely as untrusted data.
- **No CoT Leakage**: Structured `DecisionRecord` objects capture decisions without exposing internal chain-of-thought traces (`reason_summary` $\le 500$ chars).
- **Budget Tracking**: Hard limits on loop iterations, monetary cost ($USD), tool calls, and wall-clock duration.

### 2.4 Orchestration & Task Graph (Phase 4)
- **DAG Decomposition**: Kahn's algorithm guarantees cycle detection; topological sorting determines optimal execution waves.
- **Concurrency Management**: Multi-wave dispatch executing non-dependent tasks concurrently up to configured worker pool limits.

### 2.5 Non-Bypassable Risk Engine (Phase 5)
- **Dual Verification**: Assesses operations immediately prior to execution and immediately after tool output emission.
- **6 Deterministic Classifiers**: Prompt injection, data exfiltration, privilege escalation, sandbox breakout, sensitive content, and output integrity.
- **Policy Enforcement**: Dynamically maps aggregated scores to `LOW`, `MEDIUM`, `HIGH`, and `CRITICAL` levels, requiring human checkpoints where appropriate.

### 2.6 Temporal Memory (Phase 6)
- **Event Store**: Immutable, append-only log of all state changes and audit events.
- **Working Memory**: Fast Redis-backed store with automatic TTL for active agent context.
- **Episodic Memory**: Cross-execution learning database capturing past task executions, tools utilized, and distilled lessons.

### 2.7 GitHub Repository Tools (Phase 7)
- Clones target repositories into isolated temporary workspaces.
- Automatic symbol indexing, language breakdown, and directory navigation.
- Safe querying with path traversal protection.
- Manifest generation for discovered CLI and script utilities.

### 2.8 Verification & Hardening (Phase 8)
- Input sanitization and JSON schema validation.
- Impartial verifier agent evaluating output against stated acceptance criteria.
- Complete data and decision provenance tracking.
- Role-based access control (RBAC) and credentials isolation.
