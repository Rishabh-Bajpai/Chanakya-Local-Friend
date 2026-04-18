from __future__ import annotations

import asyncio
import json
from typing import Any

from chanakya.config import env_flag


def debug_enabled() -> bool:
    return env_flag("CHANAKYA_DEBUG", default=False)


def debug_log(label: str, payload: dict[str, Any] | None = None) -> None:
    if not debug_enabled():
        return
    print(f"[chanakya-debug] {label}")
    if payload:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# Transient-error retry helpers (502 / 503 / 429)
# ---------------------------------------------------------------------------

_TRANSIENT_STATUS_CODES = {502, 503, 429}
_RETRY_DELAY_SECONDS = 2.0


def is_transient_api_error(exc: Exception) -> bool:
    """Return ``True`` if *exc* looks like a retryable server / gateway error."""
    msg = str(exc).lower()
    for code in _TRANSIENT_STATUS_CODES:
        if f"error code: {code}" in msg or f"status_code={code}" in msg:
            return True
    return False


async def with_transient_retry(coro_factory, *, label: str = "agent_call"):
    """Execute *coro_factory()* with a single automatic retry on transient errors.

    ``coro_factory`` must be a zero-arg callable that returns a new awaitable
    each time (we cannot re-await an already-consumed coroutine).
    """
    try:
        return await coro_factory()
    except Exception as first_exc:
        if not is_transient_api_error(first_exc):
            raise
        debug_log(
            "transient_retry",
            {"label": label, "error": str(first_exc), "delay": _RETRY_DELAY_SECONDS},
        )
        await asyncio.sleep(_RETRY_DELAY_SECONDS)
        return await coro_factory()
