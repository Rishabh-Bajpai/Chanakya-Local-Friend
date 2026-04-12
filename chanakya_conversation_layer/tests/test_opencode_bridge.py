from __future__ import annotations

from a2a_example_app.opencode_a2a_bridge import _collect_text


def test_collect_text_reads_nested_artifact_parts() -> None:
    payload = {
        "message": {
            "artifacts": [
                {
                    "parts": [
                        {"type": "text", "text": "First reply"},
                        {"root": {"text": "Second reply"}},
                    ]
                }
            ]
        }
    }

    assert _collect_text(payload) == "First reply\nSecond reply"


def test_collect_text_reads_message_parts_payload() -> None:
    payload = {
        "parts": [
            {"type": "text", "text": "Hello from parts"},
        ]
    }

    assert _collect_text(payload) == "Hello from parts"
