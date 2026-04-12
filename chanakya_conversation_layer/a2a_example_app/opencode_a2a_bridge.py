import argparse
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    Artifact,
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils.message import new_agent_text_message


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _extract_first_user_text(context: RequestContext) -> str:
    if not context.message:
        return ""
    for part in getattr(context.message, "parts", []) or []:
        root = getattr(part, "root", None)
        text = getattr(root, "text", None)
        if text:
            return text
    return ""


def _collect_text(payload: Any) -> str:
    texts: list[str] = []

    def collect(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                texts.append(stripped)
            return
        if isinstance(value, dict):
            part_type = str(value.get("type") or "").strip().lower()
            if part_type == "text":
                collect(value.get("text"))
                return
            if "text" in value and len(value) == 1:
                collect(value.get("text"))
                return
            for key in ("parts", "artifacts", "root", "content", "message", "messages"):
                if key in value:
                    collect(value.get(key))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
            return
        for attr in ("parts", "artifacts", "root", "content", "message", "messages", "text"):
            nested = getattr(value, attr, None)
            if nested is not None and nested is not value:
                collect(nested)

    collect(payload)
    return "\n".join(dict.fromkeys(texts)).strip()


def _extract_agent_name_and_prompt(raw_prompt: str, default_agent: str) -> tuple[str, str]:
    if raw_prompt.startswith("[[opencode-agent:") and "]]" in raw_prompt:
        header, prompt = raw_prompt.split("]]", 1)
        agent_name = header.replace("[[opencode-agent:", "", 1).strip()
        return agent_name or default_agent, prompt.lstrip()
    return default_agent, raw_prompt


def _extract_request_options(
    raw_prompt: str,
    *,
    default_agent: str,
    default_model_provider: str | None,
    default_model_id: str | None,
) -> tuple[str, str, str | None, str | None, bool]:
    lines = raw_prompt.splitlines()
    if not lines or not lines[0].startswith("[[opencode-options:"):
        agent_name, clean_prompt = _extract_agent_name_and_prompt(raw_prompt, default_agent)
        return (
            agent_name,
            clean_prompt,
            default_model_provider,
            default_model_id,
            False,
        )

    header = lines[0].replace("[[opencode-options:", "", 1)
    if header.endswith("]]"):
        header = header[:-2]
    values: dict[str, str] = {}
    for part in header.split(";"):
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            values[key] = value
    clean_prompt = "\n".join(lines[1:]).lstrip()
    agent_name, final_prompt = _extract_agent_name_and_prompt(
        clean_prompt,
        values.get("agent") or default_agent,
    )
    return (
        values.get("agent") or agent_name,
        final_prompt,
        values.get("model_provider") or default_model_provider,
        values.get("model_id") or default_model_id,
        values.get("ephemeral_session", "").lower() == "true",
    )


class OpenCodeClient:
    def __init__(self) -> None:
        base_url = _env("OPENCODE_BASE_URL", "http://127.0.0.1:18496").rstrip("/")
        username = _env("OPENCODE_SERVER_USERNAME", "opencode")
        password = os.getenv("OPENCODE_SERVER_PASSWORD")
        auth = (username, password) if password else None
        timeout = float(_env("OPENCODE_HTTP_TIMEOUT", "300"))

        self.base_url = base_url
        self.agent = _env("OPENCODE_AGENT", "build")
        self.model_provider = os.getenv("OPENCODE_MODEL_PROVIDER")
        self.model_id = os.getenv("OPENCODE_MODEL_ID")
        self.auth = auth
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=self.timeout,
        )

    async def health(self) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get("/global/health")
            response.raise_for_status()
            return response.json()

    async def create_session(self, title: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post("/session", json={"title": title})
            response.raise_for_status()
            return response.json()

    async def send_message(
        self,
        session_id: str,
        text: str,
        agent_name: str | None = None,
        *,
        model_provider: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "agent": agent_name or self.agent,
            "parts": [{"type": "text", "text": text}],
        }
        chosen_provider = model_provider or self.model_provider
        chosen_model_id = model_id or self.model_id
        if chosen_provider and chosen_model_id:
            body["model"] = {
                "providerID": chosen_provider,
                "modelID": chosen_model_id,
            }
        async with self._client() as client:
            response = await client.post(f"/session/{session_id}/message", json=body)
            response.raise_for_status()
            return response.json()

    async def delete_session(self, session_id: str) -> bool:
        async with self._client() as client:
            response = await client.delete(f"/session/{session_id}")
            response.raise_for_status()
            return bool(response.json())


class OpenCodeBridgeAgent(AgentExecutor):
    def __init__(self, opencode: OpenCodeClient) -> None:
        self.opencode = opencode

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = _extract_first_user_text(context)
        session_id: str | None = None
        try:
            agent_name, clean_prompt, model_provider, model_id, _ephemeral_session = (
                _extract_request_options(
                    prompt,
                    default_agent=self.opencode.agent,
                    default_model_provider=self.opencode.model_provider,
                    default_model_id=self.opencode.model_id,
                )
            )
            session = await self.opencode.create_session(f"A2A bridge session ({agent_name})")
            session_id = session["id"]

            message = await self.opencode.send_message(
                session_id,
                clean_prompt,
                agent_name=agent_name,
                model_provider=model_provider,
                model_id=model_id,
            )
            reply = _collect_text(message) or "OpenCode returned no text parts."
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                artifacts=[
                    Artifact(
                        artifact_id=f"artifact-{context.task_id}",
                        parts=[Part(root=TextPart(text=reply))],
                        name="assistant-response",
                    )
                ],
                status=TaskStatus(
                    state=TaskState.completed,
                    message=new_agent_text_message(reply, context.context_id, context.task_id),
                ),
            )
        except Exception as exc:
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=new_agent_text_message(
                        f"OpenCode bridge error: {exc}",
                        context.context_id,
                        context.task_id,
                    ),
                ),
            )
        finally:
            if session_id:
                try:
                    await self.opencode.delete_session(session_id)
                except Exception:
                    pass
        await event_queue.enqueue_event(task)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.canceled),
            )
        )


def build_app(public_url: str | None = None) -> Any:
    opencode = OpenCodeClient()
    agent_card = AgentCard(
        name="OpenCode A2A Bridge",
        description="Expose an OpenCode agent through the A2A protocol.",
        url=public_url or _env("A2A_PUBLIC_URL", "http://127.0.0.1:18770"),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="opencode-bridge",
                name="OpenCode Bridge",
                description="Forwards tasks to an OpenCode agent over HTTP.",
                tags=["opencode", "a2a", "bridge"],
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )
    handler = DefaultRequestHandler(
        agent_executor=OpenCodeBridgeAgent(opencode),
        task_store=InMemoryTaskStore(),
    )

    @asynccontextmanager
    async def lifespan(_app: Any):
        yield

    app = A2AFastAPIApplication(agent_card, handler).build()
    app.router.lifespan_context = lifespan

    return app


app = build_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OpenCode A2A bridge server.")
    parser.add_argument("--host", default=_env("A2A_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(_env("A2A_PORT", "18770")))
    args = parser.parse_args()

    import uvicorn

    public_url = os.getenv("A2A_PUBLIC_URL", f"http://{args.host}:{args.port}")
    runtime_app = build_app(public_url=public_url)

    uvicorn.run(runtime_app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
