from __future__ import annotations

from conversation_layer.services.agent_interface import AgentInterface
from conversation_layer.services.conversation_wrapper import ConversationWrapper
from conversation_layer.services.orchestration_agent import MAFOrchestrationAgent
from conversation_layer.services.working_memory import ResponseStateStore


def with_conversation_layer(
    agent: AgentInterface,
    *,
    history_provider=None,
    orchestration_agent: MAFOrchestrationAgent | None = None,
    state_store: ResponseStateStore | None = None,
) -> ConversationWrapper:
    kwargs = {
        "agent": agent,
        "history_provider": history_provider,
        "orchestration_agent": orchestration_agent,
    }
    if state_store is not None:
        kwargs["state_store"] = state_store
    return ConversationWrapper(
        **kwargs,
    )
