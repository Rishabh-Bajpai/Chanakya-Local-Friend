import pytest
from httpx import ASGITransport, AsyncClient

from client.main import app as client_app
from server.main import app as server_app


@pytest.mark.asyncio
async def test_server_dashboard_renders():
    async with AsyncClient(transport=ASGITransport(app=server_app), base_url="http://test") as ac:
        response = await ac.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_server_status_page_renders():
    async with AsyncClient(transport=ASGITransport(app=server_app), base_url="http://test") as ac:
        response = await ac.get("/status")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def test_client_index_renders():
    async with AsyncClient(transport=ASGITransport(app=client_app), base_url="http://test") as ac:
        response = await ac.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
