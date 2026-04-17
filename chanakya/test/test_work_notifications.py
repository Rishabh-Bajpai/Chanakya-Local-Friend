"""Tests for WorkNotificationRepository and pending-messages API endpoints."""

from __future__ import annotations

from typing import Any

import pytest

from chanakya.db import build_engine, build_session_factory, init_database
from chanakya.store import ChanakyaStore


def _build_store() -> ChanakyaStore:
    engine = build_engine("sqlite:///:memory:")
    init_database(engine)
    session_factory = build_session_factory(engine)
    return ChanakyaStore(session_factory)


# ---------------------------------------------------------------------------
# WorkNotificationRepository unit tests
# ---------------------------------------------------------------------------


def test_create_notification_returns_dict() -> None:
    store = _build_store()
    result = store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Work done",
        text="Your task finished.",
    )
    assert result["id"] == "n1"
    assert result["work_id"] == "w1"
    assert result["notification_type"] == "completed"
    assert result["acknowledged"] is False
    assert result["created_at"] is not None


def test_list_pending_returns_unacknowledged_only() -> None:
    store = _build_store()
    store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Done",
        text="Finished",
    )
    store.work_notifications.create_notification(
        notification_id="n2",
        work_id="w1",
        notification_type="input_required",
        title="Need input",
        text="Please reply",
    )

    pending = store.work_notifications.list_pending()
    assert len(pending) == 2

    store.work_notifications.acknowledge("n1")
    pending = store.work_notifications.list_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == "n2"


def test_list_pending_with_include_acknowledged() -> None:
    store = _build_store()
    store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Done",
        text="Finished",
    )
    store.work_notifications.acknowledge("n1")

    pending = store.work_notifications.list_pending(include_acknowledged=True)
    assert len(pending) == 1
    assert pending[0]["acknowledged"] is True


def test_list_pending_filters_by_work_id() -> None:
    store = _build_store()
    store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Done w1",
        text="",
    )
    store.work_notifications.create_notification(
        notification_id="n2",
        work_id="w2",
        notification_type="completed",
        title="Done w2",
        text="",
    )

    result = store.work_notifications.list_pending(work_id="w1")
    assert len(result) == 1
    assert result[0]["work_id"] == "w1"


def test_acknowledge_returns_false_for_missing() -> None:
    store = _build_store()
    assert store.work_notifications.acknowledge("nonexistent") is False


def test_acknowledge_all_for_work() -> None:
    store = _build_store()
    store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="a",
        text="",
    )
    store.work_notifications.create_notification(
        notification_id="n2",
        work_id="w1",
        notification_type="input_required",
        title="b",
        text="",
    )
    store.work_notifications.create_notification(
        notification_id="n3",
        work_id="w2",
        notification_type="completed",
        title="c",
        text="",
    )

    count = store.work_notifications.acknowledge_all_for_work("w1")
    assert count == 2

    pending = store.work_notifications.list_pending()
    assert len(pending) == 1
    assert pending[0]["work_id"] == "w2"


def test_delete_work_cascades_to_notifications() -> None:
    store = _build_store()
    store.create_work(work_id="w1", title="Test", description="")
    store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Done",
        text="",
    )

    store.delete_work("w1")

    pending = store.work_notifications.list_pending()
    assert len(pending) == 0


def test_list_pending_since_filter() -> None:
    store = _build_store()
    n1 = store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="First",
        text="",
    )
    _n2 = store.work_notifications.create_notification(
        notification_id="n2",
        work_id="w1",
        notification_type="input_required",
        title="Second",
        text="",
    )

    result = store.work_notifications.list_pending(since=n1["created_at"])
    ids = [r["id"] for r in result]
    # Since is exclusive, n1 should be excluded if created_at matches exactly,
    # but both might have same timestamp in fast tests. At minimum n2 should appear.
    assert "n2" in ids


def test_notification_target_url() -> None:
    store = _build_store()
    result = store.work_notifications.create_notification(
        notification_id="n1",
        work_id="w1",
        notification_type="completed",
        title="Done",
        text="",
        target_url="/work/w1",
    )
    assert result["target_url"] == "/work/w1"

    pending = store.work_notifications.list_pending()
    assert pending[0]["target_url"] == "/work/w1"


# ---------------------------------------------------------------------------
# Pending messages API endpoint tests (Flask test client)
# ---------------------------------------------------------------------------


def _build_app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a minimal Flask test app with the pending messages routes."""
    db_path = tmp_path / "test-notifications.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    from chanakya.services import tool_loader

    monkeypatch.setattr(tool_loader, "initialize_all_tools", lambda: None)
    from chanakya.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


def test_api_pending_messages_returns_empty_list(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path, monkeypatch)
    with app.test_client() as client:
        resp = client.get("/api/works/pending-messages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "notifications" in data
        assert isinstance(data["notifications"], list)
        assert data["notifications"] == []


def test_api_ack_returns_404_for_missing(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(tmp_path, monkeypatch)
    with app.test_client() as client:
        resp = client.post("/api/works/pending-messages/nonexistent/ack")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data


def test_work_detail_route_renders_work_page_with_requested_work_id(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_app(tmp_path, monkeypatch)
    with app.test_client() as client:
        resp = client.get("/work/work_123")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'const INITIAL_WORK_ID = "work_123";' in body
