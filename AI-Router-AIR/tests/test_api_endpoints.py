"""Tests for Realtime, Batches, and Evals API proxy endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from server.main import app
from unittest.mock import AsyncMock, patch
from fastapi.responses import JSONResponse
from server.core.config import ProviderConfig
from server.core.dependencies import get_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_provider(ptype: str = "llm"):
    """Return a mock ProviderConfig for testing."""
    return ProviderConfig(name="TestP", base_url="http://test-provider/v1", api_key="na", type=ptype)


def _override_provider(ptype: str = "llm"):
    """Return an async dependency override that yields a mock provider."""
    provider = _mock_provider(ptype)

    async def _override():
        return provider

    return _override


def _json_200(body: dict | None = None):
    """Return a mock JSONResponse with 200 status."""
    return JSONResponse(content=body or {}, status_code=200)


# ---------------------------------------------------------------------------
# Realtime tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_realtime_create_secret():
    """POST /v1/realtime/client_secrets proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.realtime.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"secret": "ek_xxx"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/realtime/client_secrets", json={"model": "gpt-4o-realtime"})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_realtime_accept_call():
    """POST /v1/realtime/calls/{id}/accept proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.realtime.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/realtime/calls/call_123/accept", json={})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_realtime_hangup_call():
    """POST /v1/realtime/calls/{id}/hangup proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.realtime.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/realtime/calls/call_123/hangup", json={})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Batches tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_batch():
    """POST /v1/batches proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.batches.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "batch_1", "status": "validating"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/batches/", json={"input_file_id": "file-abc", "endpoint": "/v1/chat/completions"})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_retrieve_batch():
    """GET /v1/batches/{id} proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.batches.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "batch_1", "status": "completed"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/v1/batches/batch_1")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_batches():
    """GET /v1/batches proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.batches.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"data": []})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/v1/batches/")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_batch():
    """POST /v1/batches/{id}/cancel proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.batches.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "batch_1", "status": "cancelling"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/batches/batch_1/cancel", json={})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Evals tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_eval():
    """POST /v1/evals proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "eval_1"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/evals/", json={"name": "my-eval"})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_retrieve_eval():
    """GET /v1/evals/{id} proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "eval_1"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/v1/evals/eval_1")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_eval():
    """POST /v1/evals/{id} (update) proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "eval_1"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/evals/eval_1", json={"name": "updated"})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_eval():
    """DELETE /v1/evals/{id} proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"deleted": True})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.delete("/v1/evals/eval_1")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_evals():
    """GET /v1/evals proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"data": []})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/v1/evals/")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_eval_run():
    """POST /v1/evals/{id}/runs proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"id": "run_1"})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/v1/evals/eval_1/runs", json={"data_source": {}})
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_eval_output_items():
    """GET /v1/evals/runs/{id}/output_items proxies correctly."""
    app.dependency_overrides[get_provider] = _override_provider()
    try:
        with patch("server.api.v1.evals.proxy_engine.forward_request", new_callable=AsyncMock) as mock_fwd:
            mock_fwd.return_value = _json_200({"data": []})
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/v1/evals/runs/run_1/output_items")
            assert resp.status_code == 200
            mock_fwd.assert_called_once()
    finally:
        app.dependency_overrides.clear()
