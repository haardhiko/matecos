"""
test_api.py
===========
Unit tests for FastAPI endpoints: health, readiness, request submission, status, and cancellation.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_health_liveness(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


@pytest.mark.asyncio
async def test_health_readiness(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/readiness")
        assert response.status_code in (200, 503)
        data = response.json()
        assert "components" in data


@pytest.mark.asyncio
async def test_metrics_endpoint(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "matecos_requests_total" in response.text


@pytest.mark.asyncio
async def test_submit_request_and_get_status(app):
    headers = {"Authorization": "Bearer dev-test-token"}
    payload = {
        "text": "Please analyze this sample dataset and summarize key statistics.",
        "constraints": {
            "max_cost": 10.0,
            "max_duration_seconds": 3600,
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Submit request
        submit_resp = await client.post("/v1/requests", json=payload, headers=headers)
        assert submit_resp.status_code == 202
        submit_data = submit_resp.json()
        assert "request_id" in submit_data
        assert "execution_id" in submit_data
        assert submit_data["status"] == "CREATED"
        exec_id = submit_data["execution_id"]

        # Get status
        status_resp = await client.get(f"/v1/requests/{exec_id}", headers=headers)
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["execution_id"] == exec_id
        assert status_data["status"] == "CREATED"

        # Cancel request
        cancel_resp = await client.post(f"/v1/requests/{exec_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 202
        cancel_data = cancel_resp.json()
        assert cancel_data["status"] == "CANCELLATION_REQUESTED"
