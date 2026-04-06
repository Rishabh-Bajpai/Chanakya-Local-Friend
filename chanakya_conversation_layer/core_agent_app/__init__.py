from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from conversation_layer.integration import with_conversation_layer
from conversation_layer.services.conversation_wrapper import ConversationWrapper
from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent
from conversation_layer.services.working_memory import (
    InMemoryResponseStateStore,
    RedisResponseStateStore,
)
from core_agent_app.config import Config
from core_agent_app.db import create_session_factory as create_agent_session_factory
from core_agent_app.routes import register_routes
from core_agent_app.services.agent_session_context import (
    SQLAlchemyAgentSessionContextStore,
)
from core_agent_app.services.core_agent import (
    A2ACoreAgentAdapter,
    AgentFrameworkCoreAgentAdapter,
    BackendTargetConfig,
    RoutedCoreAgentAdapter,
)
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider
from core_agent_app.services.opencode_discovery import discover_opencode_options


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize_backend_name(value: str) -> str:
    return "openai_compatible" if value == "local" else value


def _default_openai_target(app: Flask) -> dict[str, Any]:
    return {
        "key": "default",
        "label": "OpenAI-Compatible",
        "description": "Configured OpenAI-compatible endpoint.",
        "base_url": app.config["OPENAI_BASE_URL"],
        "api_key": app.config["OPENAI_API_KEY"],
        "model": app.config["OPENAI_CHAT_MODEL_ID"],
    }


def _default_a2a_targets(app: Flask) -> list[dict[str, Any]]:
    url = str(app.config.get("A2A_AGENT_URL") or "").strip()
    if not url:
        return []
    return [
        {
            "key": "opencode",
            "label": "OpenCode A2A",
            "description": "Configured OpenCode A2A endpoint.",
            "url": url,
            "opencode_http_url": str(app.config.get("OPENCODE_BASE_URL") or "").strip(),
        }
    ]


def _build_target_metadata(target: dict[str, Any], *, app: Flask) -> dict[str, Any]:
    metadata = {
        "url": str(target.get("url") or "").strip(),
        "continuity_strategy": str(target.get("continuity_strategy") or "auto"),
        "default_remote_agent": str(target.get("default_remote_agent") or "").strip(),
        "remote_agents": list(target.get("remote_agents") or []),
        "default_model_provider": str(
            target.get("default_model_provider") or ""
        ).strip(),
        "default_model_id": str(target.get("default_model_id") or "").strip(),
        "models": list(target.get("models") or []),
        "opencode_http_url": str(
            target.get("opencode_http_url") or app.config.get("OPENCODE_BASE_URL") or ""
        ).strip(),
    }
    opencode_http_url = metadata.get("opencode_http_url") or ""
    if opencode_http_url:
        try:
            metadata.update(discover_opencode_options(opencode_http_url))
        except Exception:
            pass
    return metadata


def _build_orchestration_runtime_options(app: Flask) -> dict[str, Any]:
    models: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    def add_model(model_id: str, label: str | None = None) -> None:
        normalized = str(model_id or "").strip()
        if not normalized or normalized in seen_ids:
            return
        seen_ids.add(normalized)
        models.append({"id": normalized, "label": label or normalized})

    default_model_id = str(
        app.config.get("CONVERSATION_OPENAI_CHAT_MODEL_ID") or ""
    ).strip()
    add_model(
        default_model_id, f"Default ({default_model_id})" if default_model_id else None
    )

    for target in app.config.get("OPENAI_COMPATIBLE_TARGETS_JSON") or []:
        model_id = str(target.get("model") or "").strip()
        target_label = str(target.get("label") or target.get("key") or "").strip()
        add_model(
            model_id,
            f"{target_label}: {model_id}" if target_label and model_id else model_id,
        )

    opencode_urls: set[str] = set()
    root_opencode = str(app.config.get("OPENCODE_BASE_URL") or "").strip()
    if root_opencode:
        opencode_urls.add(root_opencode)
    for target in app.config.get("A2A_TARGETS_JSON") or []:
        discovered_url = str(target.get("opencode_http_url") or "").strip()
        if discovered_url:
            opencode_urls.add(discovered_url)

    for opencode_url in sorted(opencode_urls):
        try:
            discovered = discover_opencode_options(opencode_url)
        except Exception:
            continue
        for model in discovered.get("models") or []:
            model_id = str(model.get("id") or "").strip()
            provider = str(model.get("provider") or "").strip()
            label = str(model.get("label") or "").strip() or model_id
            if provider and model_id and label == model_id:
                label = f"{provider}/{model_id}"
            add_model(model_id, label)

    return {
        "default_model_id": default_model_id,
        "models": models,
    }


def _build_raw_agent(app: Flask, history_provider, agent_session_context_store):
    openai_targets = app.config.get("OPENAI_COMPATIBLE_TARGETS_JSON") or []
    a2a_targets = app.config.get("A2A_TARGETS_JSON") or []
    if not openai_targets:
        openai_targets = [_default_openai_target(app)]
    if not a2a_targets:
        a2a_targets = _default_a2a_targets(app)

    targets: dict[tuple[str, str], BackendTargetConfig] = {}
    for target in openai_targets:
        key = str(target.get("key") or "default").strip()
        adapter = AgentFrameworkCoreAgentAdapter(
            model=str(target.get("model") or app.config["OPENAI_CHAT_MODEL_ID"]),
            base_url=str(target.get("base_url") or app.config["OPENAI_BASE_URL"]),
            api_key=str(target.get("api_key") or app.config["OPENAI_API_KEY"]),
            debug=app.config["CHANAKYA_DEBUG"],
            env_file_path=app.config["ENV_FILE_PATH"],
            history_provider=history_provider,
        )
        targets[("openai_compatible", key)] = BackendTargetConfig(
            key=key,
            backend="openai_compatible",
            label=str(target.get("label") or key),
            description=str(
                target.get("description") or "Configured OpenAI-compatible endpoint."
            ),
            adapter=adapter,
            metadata={
                "url": str(target.get("base_url") or app.config["OPENAI_BASE_URL"]),
                "model": str(target.get("model") or app.config["OPENAI_CHAT_MODEL_ID"]),
            },
        )

    for target in a2a_targets:
        key = str(target.get("key") or "opencode").strip()
        url = str(target.get("url") or "").strip()
        if not url:
            raise ValueError(f"A2A target '{key}' is missing a URL")
        adapter = A2ACoreAgentAdapter(
            url=url,
            debug=app.config["CHANAKYA_DEBUG"],
            history_provider=history_provider,
            session_context_store=agent_session_context_store,
            a2a_agent_factory=app.config.get("A2A_AGENT_FACTORY"),
            target_key=key,
            target_label=str(target.get("label") or key),
            continuity_strategy=str(target.get("continuity_strategy") or "auto"),
            default_remote_agent=str(target.get("default_remote_agent") or "").strip()
            or None,
            default_model_provider=str(
                target.get("default_model_provider") or ""
            ).strip()
            or None,
            default_model_id=str(target.get("default_model_id") or "").strip() or None,
        )
        targets[("a2a", key)] = BackendTargetConfig(
            key=key,
            backend="a2a",
            label=str(target.get("label") or key),
            description=str(target.get("description") or "Configured A2A endpoint."),
            adapter=adapter,
            metadata=_build_target_metadata(target, app=app),
        )

    default_backend = _normalize_backend_name(
        str(app.config.get("CORE_AGENT_BACKEND", "openai_compatible"))
    )
    default_target = str(app.config.get("DEFAULT_CORE_AGENT_TARGET") or "default")
    if (default_backend, default_target) not in targets:
        if default_backend == "a2a" and a2a_targets:
            default_target = str(a2a_targets[0].get("key") or "opencode")
        else:
            default_backend = "openai_compatible"
            default_target = str(openai_targets[0].get("key") or "default")

    return RoutedCoreAgentAdapter(
        targets=targets,
        default_backend=default_backend,
        default_target=default_target,
    )


def _build_conversation_state_store(app: Flask):
    configured_store = app.config.get("CONVERSATION_STATE_STORE")
    if configured_store is not None:
        return configured_store

    backend = str(app.config.get("CONVERSATION_STATE_STORE_BACKEND", "memory")).lower()
    if backend == "redis":
        redis_url = str(
            app.config.get("CONVERSATION_STATE_STORE_REDIS_URL") or ""
        ).strip()
        if not redis_url:
            raise ValueError(
                "CONVERSATION_STATE_STORE_REDIS_URL is required when CONVERSATION_STATE_STORE_BACKEND=redis"
            )
        return RedisResponseStateStore(
            redis_url=redis_url,
            key_prefix=str(
                app.config.get("CONVERSATION_STATE_STORE_REDIS_KEY_PREFIX")
                or "conversation:working-memory:"
            ),
            ttl_seconds=int(
                app.config.get("CONVERSATION_STATE_STORE_TTL_SECONDS") or 86400
            ),
        )
    return InMemoryResponseStateStore()


def create_app(
    test_config: dict | None = None, wrapper: ConversationWrapper | None = None
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "app/templates"),
    )
    app.config.from_mapping(Config().to_flask_config())

    if test_config:
        app.config.update(test_config)

    agent_db_session_factory = app.config.get(
        "AGENT_DB_SESSION_FACTORY"
    ) or create_agent_session_factory(app.config["AGENT_DATABASE_URL"])

    history_provider = app.config.get("HISTORY_PROVIDER") or SQLAlchemyHistoryProvider(
        db_session_factory=agent_db_session_factory
    )
    agent_session_context_store = app.config.get(
        "AGENT_SESSION_CONTEXT_STORE"
    ) or SQLAlchemyAgentSessionContextStore(agent_db_session_factory)
    configured_raw_agent = app.config.get("RAW_AGENT")
    raw_agent = configured_raw_agent or _build_raw_agent(
        app,
        history_provider,
        agent_session_context_store,
    )
    conversation_orchestration_agent = app.config.get(
        "CONVERSATION_ORCHESTRATION_AGENT"
    )
    if conversation_orchestration_agent is None and app.config.get(
        "CONVERSATION_OPENAI_CHAT_MODEL_ID"
    ):
        conversation_orchestration_agent = MAFOrchestrationAgent(
            model=app.config["CONVERSATION_OPENAI_CHAT_MODEL_ID"],
            base_url=app.config["CONVERSATION_OPENAI_BASE_URL"],
            api_key=app.config["CONVERSATION_OPENAI_API_KEY"],
            env_file_path=app.config["ENV_FILE_PATH"],
            debug=app.config["CHANAKYA_DEBUG"],
        )
    conversation_state_store = _build_conversation_state_store(app)
    conversation_wrapper = wrapper or with_conversation_layer(
        raw_agent,
        history_provider=history_provider,
        orchestration_agent=conversation_orchestration_agent,
        state_store=conversation_state_store,
    )

    app.extensions["agent_db_session_factory"] = agent_db_session_factory
    app.extensions["history_provider"] = history_provider
    app.extensions["agent_session_context_store"] = agent_session_context_store
    app.extensions["raw_agent"] = raw_agent
    app.extensions["conversation_wrapper"] = conversation_wrapper
    app.extensions["conversation_state_store"] = conversation_state_store
    app.extensions["core_agent_backend"] = getattr(raw_agent, "default_backend", None)
    app.extensions["core_agent_runtime_options"] = (
        raw_agent.runtime_options() if hasattr(raw_agent, "runtime_options") else {}
    )
    app.extensions["conversation_orchestration_runtime_options"] = (
        _build_orchestration_runtime_options(app)
    )
    app.extensions["conversation_orchestration_agent"] = (
        conversation_orchestration_agent
    )

    register_routes(app)
    return app
