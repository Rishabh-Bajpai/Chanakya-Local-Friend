from __future__ import annotations

from typing import Any


def build_recovery_payload(
    *,
    error: str,
    hint: str,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": error,
        "hint": hint,
    }
    payload.update(extra)
    return payload


def build_missing_argument_payload(*, argument: str, hint: str, **extra: Any) -> dict[str, Any]:
    return build_recovery_payload(error=f"{argument} is required", hint=hint, **extra)


def build_wrong_id_payload(
    *,
    object_name: str,
    bad_id: str,
    candidates_key: str,
    candidates: list[dict[str, Any]],
    retry_hint: str,
    empty_scope_message: str,
    **extra: Any,
) -> dict[str, Any]:
    message = f"Wrong {object_name} ID: {bad_id}."
    if candidates:
        message += f" Here are available {object_name}s you can use instead."
    else:
        message += f" {empty_scope_message}"
    return build_recovery_payload(
        error=message,
        hint=retry_hint,
        **{candidates_key: candidates},
        **extra,
    )
