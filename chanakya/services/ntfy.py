from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from chanakya.config import get_ntfy_default_server_url, get_ntfy_timeout_seconds
from chanakya.domain import TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_WAITING_INPUT
from chanakya.store import ChanakyaStore

_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
_MAX_SUMMARY_LENGTH = 180


def normalize_ntfy_topic(topic: str) -> str:
    return str(topic or "").strip()


def is_valid_ntfy_topic(topic: str) -> bool:
    normalized = normalize_ntfy_topic(topic)
    return bool(_TOPIC_PATTERN.fullmatch(normalized))


def summarize_notification_text(text: str, *, limit: int = _MAX_SUMMARY_LENGTH) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return normalized[: limit - 3].rstrip() + "..."


def build_ntfy_deep_link(*, server_url: str, topic: str) -> str:
    normalized_server = str(server_url or "").rstrip("/")
    parsed = parse.urlparse(normalized_server)
    host = parsed.netloc or parsed.path
    normalized_topic = normalize_ntfy_topic(topic)
    if not host:
        raise ValueError("server_url must include a host")
    if not is_valid_ntfy_topic(normalized_topic):
        raise ValueError(
            "topic must be 6-128 chars and use only letters, numbers, dot, underscore, or dash"
        )
    return f"ntfy://{host}/{parse.quote(normalized_topic, safe='')}"


@dataclass(slots=True)
class NtfyPublishResult:
    ok: bool
    status: int | None
    body: str
    error: str | None = None


class NtfyClient:
    def __init__(self, *, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or get_ntfy_timeout_seconds()

    def publish(
        self,
        *,
        server_url: str,
        topic: str,
        message: str,
        title: str,
        priority: str,
        tags: list[str],
        click_url: str | None = None,
    ) -> NtfyPublishResult:
        normalized_server = str(server_url or "").rstrip("/")
        normalized_topic = normalize_ntfy_topic(topic)
        if not normalized_server:
            raise ValueError("server_url is required")
        if not is_valid_ntfy_topic(normalized_topic):
            raise ValueError(
                "topic must be 6-128 chars and use only letters, numbers, dot, underscore, or dash"
            )
        req = request.Request(
            url=f"{normalized_server}/{parse.quote(normalized_topic, safe='')}",
            data=message.encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "text/plain; charset=utf-8")
        req.add_header("Title", title)
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", ",".join(tag for tag in tags if tag))
        if click_url:
            req.add_header("Click", click_url)
        try:
            with request.urlopen(req, timeout=max(1, self.timeout_seconds)) as response:
                body = response.read().decode("utf-8", errors="replace")
                return NtfyPublishResult(
                    ok=True,
                    status=getattr(response, "status", None),
                    body=body,
                )
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return NtfyPublishResult(ok=False, status=exc.code, body=body, error=str(exc))
        except Exception as exc:
            return NtfyPublishResult(ok=False, status=None, body="", error=str(exc))


class NtfyNotificationDispatcher:
    channel_type = "ntfy"

    def __init__(self, store: ChanakyaStore, client: NtfyClient | None = None) -> None:
        self.store = store
        self.client = client or NtfyClient()

    def get_settings_payload(self) -> dict[str, Any]:
        row = self.store.get_notification_settings(self.channel_type)
        server_url = row.server_url if row is not None else get_ntfy_default_server_url()
        topic = row.topic if row is not None else ""
        deep_link = ""
        if topic:
            try:
                deep_link = build_ntfy_deep_link(server_url=server_url, topic=topic)
            except ValueError:
                deep_link = ""
        return {
            "channel_type": self.channel_type,
            "server_url": server_url,
            "topic": topic,
            "enabled": bool(row.enabled) if row is not None else False,
            "include_message_preview": (
                bool(row.include_message_preview) if row is not None else True
            ),
            "deep_link": deep_link,
        }

    def save_settings(
        self,
        *,
        server_url: str,
        topic: str,
        enabled: bool,
        include_message_preview: bool,
    ) -> dict[str, Any]:
        row = self.store.upsert_notification_settings(
            channel_type=self.channel_type,
            server_url=server_url.rstrip("/"),
            topic=normalize_ntfy_topic(topic),
            enabled=enabled,
            include_message_preview=include_message_preview,
        )
        return {
            "channel_type": row.channel_type,
            "server_url": row.server_url,
            "topic": row.topic,
            "enabled": row.enabled,
            "include_message_preview": row.include_message_preview,
            "deep_link": (
                build_ntfy_deep_link(server_url=row.server_url, topic=row.topic)
                if row.topic
                else ""
            ),
        }

    def delete_settings(self) -> dict[str, Any]:
        self.store.delete_notification_settings(self.channel_type)
        return self.get_settings_payload()

    def send_test_notification(self) -> NtfyPublishResult:
        settings = self.get_settings_payload()
        if not settings["enabled"]:
            raise ValueError("Phone notifications are disabled")
        return self._publish(
            server_url=str(settings["server_url"]),
            topic=str(settings["topic"]),
            title="Chanakya: Test notification",
            message="Phone notifications are configured and working.",
            priority="default",
            tags=["loudspeaker", "test_tube"],
            event_payload={"kind": "test"},
        )

    def notify_root_task_outcome(
        self,
        *,
        session_id: str,
        request_id: str,
        root_task_id: str,
        task_status: str,
        summary: str,
        work_id: str | None = None,
    ) -> None:
        settings = self.get_settings_payload()
        if not settings["enabled"]:
            self.store.log_event(
                "ntfy_notification_skipped",
                {
                    "reason": "disabled",
                    "request_id": request_id,
                    "root_task_id": root_task_id,
                    "task_status": task_status,
                },
            )
            return
        topic = str(settings["topic"])
        if not topic:
            self.store.log_event(
                "ntfy_notification_skipped",
                {
                    "reason": "missing_topic",
                    "request_id": request_id,
                    "root_task_id": root_task_id,
                    "task_status": task_status,
                },
            )
            return
        title, priority, tags = self._status_metadata(task_status)
        message = summarize_notification_text(summary)
        if work_id:
            try:
                work = self.store.get_work(work_id)
                message = summarize_notification_text(f"{work.title}: {message}")
            except KeyError:
                pass
        if not settings["include_message_preview"]:
            message = title
        self._publish(
            server_url=str(settings["server_url"]),
            topic=topic,
            title=title,
            message=message,
            priority=priority,
            tags=tags,
            event_payload={
                "kind": "root_task_outcome",
                "request_id": request_id,
                "session_id": session_id,
                "root_task_id": root_task_id,
                "task_status": task_status,
                "work_id": work_id,
            },
        )

    def _publish(
        self,
        *,
        server_url: str,
        topic: str,
        title: str,
        message: str,
        priority: str,
        tags: list[str],
        event_payload: dict[str, Any],
    ) -> NtfyPublishResult:
        result = self.client.publish(
            server_url=server_url,
            topic=topic,
            message=message,
            title=title,
            priority=priority,
            tags=tags,
        )
        event_type = "ntfy_notification_sent" if result.ok else "ntfy_notification_failed"
        self.store.log_event(
            event_type,
            {
                **event_payload,
                "topic": topic,
                "server_url": server_url,
                "title": title,
                "status": result.status,
                "error": result.error,
            },
        )
        return result

    def _status_metadata(self, task_status: str) -> tuple[str, str, list[str]]:
        if task_status == TASK_STATUS_DONE:
            return ("Chanakya: Request completed", "default", ["heavy_check_mark"])
        if task_status == TASK_STATUS_FAILED:
            return ("Chanakya: Request failed", "high", ["warning", "x"])
        if task_status == TASK_STATUS_WAITING_INPUT:
            return ("Chanakya: Input needed", "high", ["warning"])
        return ("Chanakya: Request updated", "default", ["loudspeaker"])
