import pytest
from httpx import AsyncClient, ASGITransport
from server.main import app


class TestHealthAPI:
    """Test health check endpoint"""

    @pytest.mark.asyncio
    async def test_health_check_returns_ok(self):
        """Test that /health endpoint returns ok status"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_no_authentication_required(self):
        """Test that health check doesn't require authentication"""
        # Health endpoints should be publicly accessible
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_check_json_response(self):
        """Test that health check returns valid JSON"""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

        assert response.headers["content-type"].startswith("application/json")
        # Should not raise exception
        data = response.json()
        assert isinstance(data, dict)
