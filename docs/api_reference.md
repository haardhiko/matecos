# MATECOS API Reference

All requests must use JSON payloads and return RFC 9457 formatted error structures on failure.

---

## 1. Authentication
Endpoints require an `Authorization: Bearer <token>` or `X-API-Key: <key>` header. In development mode (`APP_ENV=development`), requests default to an administrative dev identity.

---

## 2. Health & Monitoring

### `GET /health`
Liveness probe indicating process availability.
- **Response**: `200 OK`
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "timestamp": "2026-09-22T17:00:00Z",
  "components": {}
}
```

### `GET /readiness`
Readiness probe verifying database and Redis connectivity.
- **Response**: `200 OK` (or `503 Service Unavailable`)

### `GET /metrics`
Prometheus text format metrics outputting request counters and latencies.

---

## 3. Requests & Executions

### `POST /v1/requests`
Submits a natural language goal to the request gateway.

- **Request Body**:
```json
{
  "text": "Analyze the customer churn dataset and generate a summary report.",
  "idempotency_key": "unique-client-key-1234",
  "constraints": {
    "max_cost": 5.00,
    "max_duration_seconds": 1800
  },
  "attachments": []
}
```
- **Response**: `202 Accepted`
```json
{
  "request_id": "01J8ABCDEF1234567890ABCDEF",
  "execution_id": "01J8ABCDEF1234567890ABCDEG",
  "status": "CREATED",
  "status_url": "/v1/requests/01J8ABCDEF1234567890ABCDEG",
  "created_at": "2026-09-22T17:00:00Z"
}
```

### `GET /v1/requests/{execution_id}`
Retrieves real-time status, active tasks, allocated agents, and pending approvals.

- **Response**: `200 OK`
```json
{
  "execution_id": "01J8ABCDEF1234567890ABCDEG",
  "request_id": "01J8ABCDEF1234567890ABCDEF",
  "status": "RUNNING",
  "goal_text": "Analyze the customer churn dataset...",
  "created_at": "2026-09-22T17:00:00Z",
  "updated_at": "2026-09-22T17:00:05Z",
  "tasks": [
    {
      "task_id": "task_1",
      "role": "analyst",
      "objective": "Profile churn CSV data",
      "status": "COMPLETED"
    }
  ],
  "agents": [],
  "pending_approvals": [],
  "total_cost_usd": 0.042
}
```

### `POST /v1/requests/{execution_id}/cancel`
Requests graceful termination of an active execution.
- **Response**: `202 Accepted`

### `POST /v1/requests/{execution_id}/approve/{approval_id}`
Approves a pending human-in-the-loop checkpoint triggered by the risk engine.
- **Request Body**: `{"notes": "Approved after reviewing output schema."}`
- **Response**: `200 OK`

### `POST /v1/requests/{execution_id}/reject/{approval_id}`
Rejects a pending action, halting the blocked agent path.
- **Request Body**: `{"reason": "Operation exceeds data sensitivity scope."}`
- **Response**: `200 OK`

---

## 4. Tool Registry

### `GET /v1/tools`
Search and list registered tool manifests.
- **Query Parameters**: `capabilities`, `name_pattern`, `max_risk_level`

### `POST /v1/tools`
Register or update a tool manifest (admin only).
- **Request Body**: `ToolManifest` schema.
