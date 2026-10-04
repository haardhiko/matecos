"""Unit tests for MATECOS conversations and execution traces."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_conversation_lifecycle(client: TestClient):
    # 1. Create a new conversation
    res = client.post("/v1/conversations", json={"title": "Test Math Session"})
    assert res.status_code == 201
    conv = res.json()
    conv_id = conv["id"]
    assert conv["title"] == "Test Math Session"
    assert conv["messages"] == []

    # 2. List conversations
    res = client.get("/v1/conversations")
    assert res.status_code == 200
    summaries = res.json()
    assert any(s["id"] == conv_id for s in summaries)

    # 3. Send a message that triggers a real tool (math calculator)
    res = client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "calculate 40 * 2.5 + 50"},
    )
    assert res.status_code == 200
    msg = res.json()
    assert msg["role"] == "assistant"
    assert "150" in msg["content"]
    assert msg["trace"] is not None
    assert msg["trace"]["tool_id"] == "math.calculator"
    assert msg["trace"]["status"] == "SUCCEEDED"
    assert len(msg["trace"]["stages"]) >= 6

    # Verify stage names in execution trace
    stage_names = [s["stage"] for s in msg["trace"]["stages"]]
    assert "REQUEST" in stage_names
    assert "SEARCH" in stage_names
    assert "TOOL" in stage_names
    assert "EXECUTION" in stage_names
    assert "RESULT" in stage_names

    # 4. Fetch full conversation
    res = client.get(f"/v1/conversations/{conv_id}")
    assert res.status_code == 200
    full_conv = res.json()
    assert len(full_conv["messages"]) == 2  # user + assistant

    # 5. Rename conversation
    res = client.patch(
        f"/v1/conversations/{conv_id}",
        json={"title": "Renamed Math Session"},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Renamed Math Session"

    # 6. Delete conversation
    res = client.delete(f"/v1/conversations/{conv_id}")
    assert res.status_code == 204

    # 7. Verify deletion
    res = client.get(f"/v1/conversations/{conv_id}")
    assert res.status_code == 404
