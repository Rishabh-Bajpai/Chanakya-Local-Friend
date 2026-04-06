from __future__ import annotations

import re
from dataclasses import dataclass

from conversation_layer.schemas import ChatRequest, ChatResponse
from conversation_layer.services.conversation_wrapper import ConversationWrapper
from core_agent_app.db import create_session_factory
from core_agent_app.services.history_provider import SQLAlchemyHistoryProvider


@dataclass(slots=True)
class FakeCoreAgent:
    response_text: str
    calls: list[ChatRequest]

    def respond(self, chat_request: ChatRequest) -> ChatResponse:
        self.calls.append(chat_request)
        return ChatResponse(
            session_id=chat_request.session_id,
            response=self.response_text,
            metadata={"source": "fake_core"},
        )


class FakePlanner:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def plan(self, *, task: str, instructions: str, payload: dict) -> dict:
        self.calls.append(
            {
                "task": task,
                "instructions": instructions,
                "payload": payload,
            }
        )
        return self.responses.pop(0)

    def plan_with_model(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict,
        model_id: str | None,
    ) -> dict:
        self.calls.append(
            {
                "task": task,
                "instructions": instructions,
                "payload": payload,
                "model_id": model_id,
            }
        )
        return self.responses.pop(0)


def _build_wrapper(tmp_path, *, core_text: str, planner_responses: list[dict]):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'wrapper.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)
    core_agent = FakeCoreAgent(response_text=core_text, calls=[])
    planner = FakePlanner(planner_responses)
    wrapper = ConversationWrapper(
        agent=core_agent,
        history_provider=history_provider,
        orchestration_agent=planner,
    )
    return wrapper, core_agent, planner, history_provider


def test_wrapper_calls_core_and_queues_remaining_messages(tmp_path):
    wrapper, core_agent, planner, history_provider = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "What is 5+6?",
                "queue_action": "replace",
            },
            {
                "reasoning": "Split into two messages.",
                "messages": [
                    {"text": "It is 11.", "delay_ms": 0},
                    {"text": "That was straightforward.", "delay_ms": 5000},
                ],
            },
        ],
    )

    response = wrapper.handle(ChatRequest(session_id="s1", message="5+6=?"))
    memory = wrapper.list_debug_view("s1")
    history = history_provider.list_messages("s1")

    assert len(core_agent.calls) == 1
    assert response.response == "It is 11."
    assert [item.text for item in response.messages] == ["It is 11."]
    assert response.metadata["pending_delivery_count"] == 1
    assert response.metadata["interrupt_type"] == "reset_and_new_query"
    assert memory["core_agent_called"] is True
    assert memory["topic_state"] == "active"
    assert [item["text"] for item in memory["pending_messages"]] == [
        "That was straightforward."
    ]
    assert [item["text"] for item in history] == ["It is 11."]
    assert [call["task"] for call in planner.calls] == [
        "Working memory routing",
        "Conversation delivery planning",
    ]
    assert "conversation_preferences" not in planner.calls[0]["payload"]
    assert (
        "Each message should usually be short, conversational, and at most 1 to 3 sentences."
        in planner.calls[1]["instructions"]
    )
    assert (
        "0 to 3 human-sounding future assistant messages"
        not in planner.calls[1]["instructions"]
    )


def test_wrapper_uses_selected_orchestration_model_override(tmp_path):
    wrapper, _, planner, _ = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "hello",
                "queue_action": "replace",
            },
            {
                "reasoning": "Single message.",
                "messages": [
                    {"text": "It works.", "delay_ms": 0},
                ],
            },
        ],
    )

    response = wrapper.handle(
        ChatRequest(
            session_id="s1",
            message="hello",
            metadata={"conversation_orchestration_model_id": "qwen/qwen3.5-9b"},
        )
    )

    assert planner.calls[0]["model_id"] == "qwen/qwen3.5-9b"
    assert planner.calls[1]["model_id"] == "qwen/qwen3.5-9b"
    assert response.metadata["conversation_orchestration_model_id"] == "qwen/qwen3.5-9b"


def test_wrapper_delivers_next_message_and_rewrites_history(tmp_path):
    wrapper, _, _, history_provider = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "hello",
                "queue_action": "replace",
            },
            {
                "reasoning": "Split into two messages.",
                "messages": [
                    {"text": "First visible answer.", "delay_ms": 0},
                    {"text": "Second visible answer.", "delay_ms": 5000},
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="hello"))
    state = wrapper.state_store.get("s1")
    state.pending_messages[0]["available_at"] = "2000-01-01T00:00:00+00:00"
    wrapper.state_store.save("s1", state)

    result = wrapper.deliver_next_message("s1")
    history = history_provider.list_messages("s1")

    assert result["status"] == "delivered"
    assert result["message"]["text"] == "Second visible answer."
    assert [item["text"] for item in history] == [
        "First visible answer.",
        "Second visible answer.",
    ]
    assert result["working_memory"]["pending_messages"] == []


def test_manual_pause_blocks_next_delivery(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "hello",
                "queue_action": "replace",
            },
            {
                "reasoning": "Split into two messages.",
                "messages": [
                    {"text": "First answer.", "delay_ms": 0},
                    {"text": "Second answer.", "delay_ms": 5000},
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="hello"))
    wrapper.request_manual_pause("s1")

    result = wrapper.deliver_next_message("s1")

    assert result["status"] == "paused"
    assert result["working_memory"]["manual_pause_requested"] is True


def test_interruption_can_reuse_working_memory_without_new_core_call(tmp_path):
    wrapper, core_agent, _, history_provider = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Explain recursion",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial plan.",
                "messages": [
                    {"text": "Recursion is a function calling itself.", "delay_ms": 0},
                    {"text": "A base case stops it.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.93,
                "use_core_agent": False,
                "reasoning": "Use the current response memory.",
                "message_for_core_agent": "",
                "queue_action": "continue_from_working_memory",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Adapt to interruption.",
                "messages": [
                    {
                        "text": "Sure, the short version is: recursion repeats until a base case stops it.",
                        "delay_ms": 0,
                    },
                ],
            },
        ],
    )

    first = wrapper.handle(ChatRequest(session_id="s1", message="Explain recursion"))
    second = wrapper.handle(
        ChatRequest(session_id="s1", message="Short version please")
    )
    history = history_provider.list_messages("s1")

    assert first.metadata["pending_delivery_count"] == 1
    assert len(core_agent.calls) == 1
    assert second.metadata["core_agent_called"] is False
    assert second.metadata["cancelled_pending_count"] == 1
    assert second.metadata["queue_cleared_reason"] == "new_user_message"
    assert second.metadata["interrupt_type"] == "adapt_remaining"
    assert second.metadata["same_topic"] is True
    assert [item["role"] for item in history] == ["assistant", "user", "assistant"]
    assert history[-1]["text"] == (
        "Sure, the short version is: recursion repeats until a base case stops it."
    )


def test_misclassified_same_topic_is_forced_to_new_topic_for_fresh_query(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="The current time is 22:26:45 UTC.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Initial topic.",
                "message_for_core_agent": "tell me a shloka",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial delivery.",
                "messages": [{"text": "Here is one shloka.", "delay_ms": 0}],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Wrongly treated as same topic.",
                "message_for_core_agent": "What time is it?",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Time answer.",
                "messages": [
                    {"text": "The current time is 22:26:45 UTC.", "delay_ms": 0}
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="tell me a shloka"))
    second = wrapper.handle(ChatRequest(session_id="s1", message="What time is it?"))
    memory = wrapper.list_debug_view("s1")

    assert len(core_agent.calls) == 2
    assert second.metadata["interrupt_type"] == "reset_and_new_query"
    assert second.metadata["same_topic"] is False
    assert [item["text"] for item in memory["delivered_messages"]] == [
        "The current time is 22:26:45 UTC."
    ]


def test_non_core_same_topic_plan_is_not_replaced_by_old_core_response(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="Recursion is a function calling itself. A base case stops it.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Explain recursion",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial plan.",
                "messages": [
                    {
                        "text": "Recursion is a function calling itself.",
                        "delay_ms": 0,
                    },
                    {"text": "A base case stops it.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.9,
                "use_core_agent": False,
                "reasoning": "Use working memory only.",
                "message_for_core_agent": "",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer the simplification directly.",
                "messages": [
                    {
                        "text": "Short version: it repeats until a stopping point.",
                        "delay_ms": 0,
                    }
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="Explain recursion"))
    response = wrapper.handle(ChatRequest(session_id="s1", message="Short version"))

    assert len(core_agent.calls) == 1
    assert (
        response.messages[0].text == "Short version: it repeats until a stopping point."
    )


def test_acknowledgement_continues_pending_queue_without_restarting(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Explain ML and DL",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial plan.",
                "messages": [
                    {"text": "ML learns patterns from examples.", "delay_ms": 0},
                    {
                        "text": "DL uses neural networks for deeper patterns.",
                        "delay_ms": 5000,
                    },
                ],
            },
            {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.98,
                "use_core_agent": False,
                "reasoning": "Keep going.",
                "message_for_core_agent": "",
                "queue_action": "preserve_pending",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": True,
            },
        ],
    )

    first = wrapper.handle(ChatRequest(session_id="s1", message="Explain ML and DL"))
    second = wrapper.handle(ChatRequest(session_id="s1", message="nice"))
    memory = wrapper.list_debug_view("s1")

    assert first.metadata["pending_delivery_count"] == 1
    assert len(core_agent.calls) == 1
    assert second.metadata["core_agent_called"] is False
    assert second.metadata["interrupt_type"] == "ack_continue"
    assert second.metadata["cancelled_pending_count"] == 0
    assert second.messages == []
    assert [item["text"] for item in memory["pending_messages"]] == [
        "DL uses neural networks for deeper patterns."
    ]
    assert memory["latest_user_message"] == "nice"


def test_acknowledgement_after_manual_pause_preserves_pending_queue(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Tell me jokes",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial plan.",
                "messages": [
                    {"text": "Sure! Here are some jokes:", "delay_ms": 0},
                    {"text": "Joke one.", "delay_ms": 5000},
                    {"text": "Joke two.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": False,
                "reasoning": "Keep the remaining queue.",
                "message_for_core_agent": "",
                "queue_action": "preserve_pending",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": True,
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="Tell me jokes"))
    wrapper.request_manual_pause("s1")
    second = wrapper.handle(ChatRequest(session_id="s1", message="ok"))
    memory = wrapper.list_debug_view("s1")

    assert second.metadata["interrupt_type"] == "ack_continue"
    assert second.metadata["pending_delivery_count"] == 2
    assert memory["queue_cleared_reason"] is None
    assert [item["text"] for item in memory["pending_messages"]] == [
        "Joke one.",
        "Joke two.",
    ]


def test_next_after_manual_pause_preserves_pending_queue_in_fallback_mode(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'wrapper.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)
    core_agent = FakeCoreAgent(
        response_text=(
            "Here are three points you can use right now. "
            "First point gives context so the idea is easier to anchor in memory. "
            "Second point adds practical detail so you can apply it immediately in a real task. "
            "Third point closes with a quick rule of thumb you can reuse later."
        ),
        calls=[],
    )
    wrapper = ConversationWrapper(
        agent=core_agent,
        history_provider=history_provider,
        orchestration_agent=None,
    )

    first = wrapper.handle(ChatRequest(session_id="s1", message="Explain this"))
    assert first.metadata["pending_delivery_count"] >= 1
    wrapper.request_manual_pause("s1")

    second = wrapper.handle(ChatRequest(session_id="s1", message="next"))
    memory = wrapper.list_debug_view("s1")

    assert len(core_agent.calls) == 1
    assert second.metadata["interrupt_type"] == "ack_continue"
    assert second.metadata["core_agent_called"] is False
    assert (
        second.metadata["pending_delivery_count"]
        == first.metadata["pending_delivery_count"]
    )
    assert memory["pending_messages"]


def test_same_topic_adaptation_can_merge_new_core_response_without_replaying_delivered_content(
    tmp_path,
):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="Original core answer.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Explain recursion",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial plan.",
                "messages": [
                    {"text": "Recursion is a function calling itself.", "delay_ms": 0},
                    {"text": "A base case stops it.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Need more detail on the same topic.",
                "message_for_core_agent": "Explain recursion in more depth with an example",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Add deeper follow-up only.",
                "messages": [
                    {
                        "text": "A simple example is factorial, where 4! calls 3! then 2! then 1!.",
                        "delay_ms": 0,
                    },
                    {
                        "text": "The base case keeps the function from running forever.",
                        "delay_ms": 5000,
                    },
                ],
            },
        ],
    )
    core_agent.response_text = "Recursion can be illustrated with factorial."

    wrapper.handle(ChatRequest(session_id="s1", message="Explain recursion"))
    second = wrapper.handle(
        ChatRequest(session_id="s1", message="Explain more with an example")
    )
    memory = wrapper.list_debug_view("s1")

    assert len(core_agent.calls) == 2
    assert second.metadata["interrupt_type"] == "adapt_remaining"
    assert second.metadata["same_topic"] is True
    assert second.metadata["core_agent_called"] is True
    assert (
        memory["delivered_messages"][0]["text"]
        == "Recursion is a function calling itself."
    )
    assert memory["delivered_messages"][1]["text"] == (
        "A simple example is factorial, where 4! calls 3! then 2! then 1!."
    )
    assert (
        memory["remaining_summary"]
        == "The base case keeps the function from running forever."
    )


def test_same_topic_continue_does_not_duplicate_prior_assistant_messages_in_history(
    tmp_path,
):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'wrapper.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)

    class RecordingFakeCoreAgent:
        def __init__(self, response_text: str) -> None:
            self.response_text = response_text
            self.calls: list[ChatRequest] = []

        def respond(self, chat_request: ChatRequest) -> ChatResponse:
            self.calls.append(chat_request)
            history_provider.append_conversation_turn(
                chat_request.session_id,
                user_message=chat_request.message,
                assistant_message=self.response_text,
            )
            return ChatResponse(
                session_id=chat_request.session_id,
                response=self.response_text,
                metadata={"source": "fake_core"},
            )

    planner = FakePlanner(
        [
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "how about India?",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial travel answer.",
                "messages": [
                    {
                        "text": "India is such a fascinating country-so much culture, history, and natural beauty packed into one place!",
                        "delay_ms": 0,
                    },
                    {
                        "text": "If you're thinking about visiting, the best time is usually October through March when it's cooler and more pleasant across most regions.",
                        "delay_ms": 0,
                    },
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.94,
                "use_core_agent": True,
                "reasoning": "Continue the same topic without replaying prior content.",
                "message_for_core_agent": "continue...",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Add one new follow-up message only.",
                "messages": [
                    {
                        "text": "Would you like suggestions on specific cities or experiences-like a temple tour in Varanasi, a beach getaway in Goa, or trekking in the Himalayas?",
                        "delay_ms": 0,
                    },
                ],
            },
        ]
    )
    core_agent = RecordingFakeCoreAgent(response_text="India is fascinating.")
    wrapper = ConversationWrapper(
        agent=core_agent,
        history_provider=history_provider,
        orchestration_agent=planner,
    )
    core_agent.response_text = (
        "Would you like suggestions on specific cities or experiences?"
    )

    wrapper.handle(ChatRequest(session_id="s1", message="how about India?"))
    second = wrapper.handle(ChatRequest(session_id="s1", message="continue..."))
    history = history_provider.list_messages("s1")

    assert second.messages[0].text.startswith(
        "Would you like suggestions on specific cities"
    )
    assert [item["text"] for item in history] == [
        "how about India?",
        "India is such a fascinating country-so much culture, history, and natural beauty packed into one place!",
        "continue...",
        "Would you like suggestions on specific cities or experiences-like a temple tour in Varanasi, a beach getaway in Goa, or trekking in the Himalayas?",
    ]


def test_pending_item_constraint_triggers_same_topic_core_adaptation(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="Initial joke list.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Tell me 5 jokes",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial joke plan.",
                "messages": [
                    {
                        "text": "Here are five light-hearted jokes for you:",
                        "delay_ms": 0,
                    },
                    {"text": "1. Skeleton joke.", "delay_ms": 5000},
                    {"text": "2. Impasta joke.", "delay_ms": 5000},
                    {"text": "3. Scarecrow joke.", "delay_ms": 5000},
                    {"text": "4. Eyebrow joke.", "delay_ms": 5000},
                    {"text": "5. Switzerland joke.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "adapt_remaining_with_core",
                "same_topic": True,
                "topic_continuity_confidence": 0.97,
                "use_core_agent": True,
                "reasoning": "Replace the next pending joke under the user's constraint.",
                "message_for_core_agent": "Continue the current joke list but do not make the next joke about Switzerland.",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Replace only the remaining joke.",
                "messages": [
                    {
                        "text": "5. Why did the math book look sad? Because it had too many problems.",
                        "delay_ms": 0,
                    },
                ],
            },
        ],
    )
    core_agent.response_text = (
        "5. Why did the math book look sad? Because it had too many problems."
    )

    wrapper.handle(ChatRequest(session_id="s1", message="tell me 5 jokes"))
    state = wrapper.state_store.get("s1")
    delivered_type = type(state.delivered_messages[0])
    state.pending_messages = [
        {
            "text": "5. Switzerland joke.",
            "delay_ms": 5000,
            "available_at": "2000-01-01T00:00:00+00:00",
        },
    ]
    state.delivered_messages = [
        delivered_type(text="Here are five light-hearted jokes for you:", delay_ms=0),
        delivered_type(text="1. Skeleton joke.", delay_ms=5000),
        delivered_type(text="2. Impasta joke.", delay_ms=5000),
        delivered_type(text="3. Scarecrow joke.", delay_ms=5000),
        delivered_type(text="4. Eyebrow joke.", delay_ms=5000),
    ]
    wrapper.state_store.save("s1", state)

    response = wrapper.handle(
        ChatRequest(
            session_id="s1",
            message="Don't dare to tell me next joke about Switzerland",
        )
    )
    memory = wrapper.list_debug_view("s1")

    assert response.metadata["interrupt_type"] == "adapt_remaining_with_core"
    assert response.metadata["same_topic"] is True
    assert response.metadata["core_agent_called"] is True
    assert len(core_agent.calls) == 2
    assert response.messages[0].text == (
        "5. Why did the math book look sad? Because it had too many problems."
    )
    assert memory["delivered_messages"][-1]["text"] == (
        "5. Why did the math book look sad? Because it had too many problems."
    )
    assert memory["topic_state"] == "active"
    assert memory["queue_cleared_reason"] == "new_user_message"


def test_numbered_core_response_uses_planner_delivery_plan(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=(
            "Sure! Here are five interesting facts:\n\n"
            "1. **Honey never spoils**: Ancient honey can still be edible.\n"
            "2. **Octopuses have three hearts**: And blue blood.\n"
            "3. **The shortest war** lasted 38 minutes.\n"
            "4. **Bananas are berries**: But strawberries are not.\n"
            "5. **There are more stars** than grains of sand on Earth.\n\n"
            "Let me know if you'd like more!"
        ),
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "tell me 5 facts",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "The planner owns delivery chunking for this response.",
                "messages": [
                    {
                        "text": "Here’s another fun one: honey never spoils.",
                        "delay_ms": 0,
                    },
                    {"text": "Octopuses have three hearts.", "delay_ms": 5000},
                    {"text": "Bananas are berries.", "delay_ms": 5000},
                ],
            },
        ],
    )

    response = wrapper.handle(ChatRequest(session_id="s1", message="tell me 5 facts"))
    memory = wrapper.list_debug_view("s1")

    assert response.messages[0].text == "Here’s another fun one: honey never spoils."
    assert response.metadata["pending_delivery_count"] == 2
    assert [item["text"] for item in memory["pending_messages"]] == [
        "Octopuses have three hearts.",
        "Bananas are berries.",
    ]
    assert memory["latest_core_response"].startswith(
        "Sure! Here are five interesting facts:"
    )


def test_incomplete_numbered_plan_restores_full_core_response(tmp_path):
    core_text = (
        'Here are 8 short answers to "What is life?":\n\n'
        "1. Biological definition: A self-sustaining chemical system capable of Darwinian evolution.\n"
        "2. Philosophical view: Consciousness, experience, and pursuit of meaning.\n"
        "3. Scientific perspective: Organized matter that grows and responds to stimuli.\n"
        "4. Emergent property: Complex molecular interactions forming dynamic systems.\n"
        "5. Cosmic phenomenon: A rare outcome of physical laws enabling complexity.\n"
        "6. Existential concept: Being alive with awareness and mortality.\n"
        "7. Information view: Storage, processing, and transmission of genetic information.\n"
        "8. Spiritual interpretation: A journey of soul evolution."
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=core_text,
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "what is life. give me 8 short answers",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner accidentally returned only half the list.",
                "messages": [
                    {
                        "text": "Here are 8 short answers: 1. Biological definition... 2. Philosophical view... 3. Scientific perspective... 4. Emergent property...",
                        "delay_ms": 0,
                    }
                ],
            },
        ],
    )

    wrapper.handle(
        ChatRequest(session_id="s1", message="what is life. give me 8 short answers")
    )
    memory = wrapper.list_debug_view("s1")
    combined = " ".join(item["text"] for item in memory["planned_messages"])

    assert "8." in combined
    assert memory["pending_messages"]


def test_fallback_split_keeps_numbered_items_intact(tmp_path):
    core_text = (
        "Sure! Here are 10 random fun facts:\n\n"
        "1. Honey never spoils - archaeological finds have eaten honey over 3000 years old.\n"
        "2. Octopuses have three hearts and nine brains.\n"
        "3. The smallest bird, the bee hummingbird, weighs less than a dollar.\n"
        "4. Bananas are curved because they grow toward the light.\n"
        '5. A group of flamingos is called a "flamboyance."\n'
        "6. Wombat poop is cube-shaped to prevent rolling.\n"
        "7. Cows have best friends and get stressed when separated.\n"
        "8. Jellyfish have been around longer than trees.\n"
        "9. The Eiffel Tower can be 15 cm taller in summer due to expansion.\n"
        "10. A day on Venus is longer than a year on Venus.\n\n"
        "Want more facts, or something about a specific topic?"
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=core_text,
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "tell me 10 facts",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Single long planned message; wrapper should split safely.",
                "messages": [{"text": core_text, "delay_ms": 0}],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="tell me 10 facts"))
    memory = wrapper.list_debug_view("s1")
    planned_texts = [item["text"] for item in memory["planned_messages"]]

    assert all(not re.search(r"\b\d+\.$", text.strip()) for text in planned_texts)
    assert any(
        "4. Bananas are curved because they grow toward the light." in text
        for text in planned_texts
    )


def test_ack_continue_without_pending_forces_core_for_real_request(tmp_path):
    wrapper, core_agent, planner, _ = _build_wrapper(
        tmp_path,
        core_text="Here are more facts.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "tell me 10 facts",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Initial response.",
                "messages": [{"text": "Fact batch A.", "delay_ms": 0}],
            },
            {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.2,
                "use_core_agent": False,
                "reasoning": "User asked for more.",
                "message_for_core_agent": "",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Follow-up response.",
                "messages": [{"text": "Fact batch B.", "delay_ms": 0}],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="tell me 10 facts"))
    second = wrapper.handle(ChatRequest(session_id="s1", message="tell me 5 more"))

    assert second.metadata["interrupt_type"] == "adapt_remaining"
    assert second.metadata["core_agent_called"] is True
    assert len(core_agent.calls) == 2
    assert "No pending queue exists" in second.metadata["wm_manager"]["reasoning"]
    assert planner.calls[2]["task"] == "Working memory routing"


def test_splitter_avoids_colon_dot_and_double_period_artifacts(tmp_path):
    core_text = (
        "Sure! Here are 10 quick fun facts:\n\n"
        "1. Honey never spoils - archaeological finds have eaten honey over 3000 years old.\n"
        '2. A group of flamingos is called a "flamboyance."\n'
        "3. Wombat poop is cube-shaped to prevent rolling.\n"
        "4. Cows have best friends and get stressed when separated.\n"
        "5. Jellyfish have been around longer than trees.\n"
        "6. The Eiffel Tower can be taller in summer.\n"
        "7. A day on Venus is longer than a year on Venus.\n"
        "8. Octopuses have three hearts.\n"
        "9. Bananas are curved because they grow toward light.\n"
        "10. The bee hummingbird is tiny."
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=core_text,
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "tell me 10 quick facts",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Single long message to force splitting.",
                "messages": [{"text": core_text, "delay_ms": 0}],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="tell me 10 quick facts"))
    memory = wrapper.list_debug_view("s1")
    joined = " ".join(item["text"] for item in memory["planned_messages"])

    assert ":." not in joined
    assert '.".' not in joined


def test_requested_more_count_trims_overproduced_numbered_items(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=(
            'Sure! Here are 5 more short answers to "What is life?":\n\n'
            "5. A process of energy transformation that maintains order against entropy.\n"
            "6. A network of cells and genes working together for survival and reproduction.\n"
            "7. The ability to adapt, evolve, and respond to environmental changes.\n"
            "8. A temporary state of organized complexity in the universe.\n"
            "9. A biological phenomenon marked by metabolism, growth, and response.\n"
            "10. A journey from origin to extinction, shaped by natural laws and chance."
        ),
        planner_responses=[
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Need fresh facts.",
                "message_for_core_agent": "good! tell 5 more",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner returned full response.",
                "messages": [
                    {
                        "text": "5. A process of energy transformation that maintains order against entropy. 6. A network of cells and genes working together for survival and reproduction. 7. The ability to adapt, evolve, and respond to environmental changes. 8. A temporary state of organized complexity in the universe. 9. A biological phenomenon marked by metabolism, growth, and response. 10. A journey from origin to extinction, shaped by natural laws and chance.",
                        "delay_ms": 0,
                    }
                ],
            },
        ],
    )

    response = wrapper.handle(ChatRequest(session_id="s1", message="good! tell 5 more"))
    memory = wrapper.list_debug_view("s1")
    combined = " ".join(item["text"] for item in memory["planned_messages"])

    assert "5." in combined
    assert "9." in combined
    assert "10." not in combined
    assert response.metadata["core_agent_called"] is True


def test_poem_layout_is_preserved_when_split(tmp_path):
    core_text = (
        "Of course! Here's a short, original poem for you:\n\n"
        "---\n\n"
        "**The Whisper of the Wind**\n"
        "Beneath the sky so wide and blue,\n"
        "Where clouds drift slow in morning dew,\n"
        "A breeze sings soft, a lullaby,\n"
        "To cradled hills and sleeping sky.\n\n"
        "It dances through the trees at dawn,\n"
        "And hums where ancient rivers run.\n"
        "It carries dreams from far away-\n"
        "Like stars that fall to greet the day.\n\n"
        "So close your eyes, let silence grow,\n"
        "And feel the world begin to glow.\n"
        "For life is but a song, you see-\n"
        "A poem sung by you and me.\n\n"
        "---\n\n"
        "I hope you enjoyed it!"
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=core_text,
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "now sing a poem",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner returned one long formatted message.",
                "messages": [{"text": core_text, "delay_ms": 0}],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="now sing a poem"))
    memory = wrapper.list_debug_view("s1")
    first_chunk = memory["planned_messages"][0]["text"]
    combined = "\n\n".join(item["text"] for item in memory["planned_messages"])

    assert "**The Whisper of the Wind**" in combined
    assert "Wind** Beneath" not in combined
    assert "\nBeneath the sky so wide and blue," in combined
    assert "\n\n---\n\n" in combined
    assert (
        "\n\nIt dances through the trees at dawn," in first_chunk
        or len(memory["planned_messages"]) > 1
    )


def test_detailed_request_restores_full_core_coverage(tmp_path):
    detailed_core = (
        "Sure! Here's a more detailed overview of MS Dhoni:\n\n"
        "Mahendra Singh Dhoni is a former international cricketer and one of the most successful captains in cricket history. "
        "He led India across formats and is known for calm decision-making under pressure.\n\n"
        "Early in his career, he emerged as a powerful finisher and a highly effective wicketkeeper. "
        "He became captain in 2007 and guided India to major ICC tournament wins in 2007, 2011, and 2013.\n\n"
        "His leadership style emphasized tactical flexibility, trust in bowlers, and composure in high-stakes moments. "
        "As a batter, he was known for chasing under pressure and finishing games in limited-overs cricket.\n\n"
        "He also built a lasting legacy in the IPL with Chennai Super Kings, where his captaincy and game awareness "
        "became central to the team's identity."
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=detailed_core,
        planner_responses=[
            {
                "interrupt_type": "adapt_remaining_with_core",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Need detailed follow-up.",
                "message_for_core_agent": "Now I want a detailed one",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner accidentally returned a short version.",
                "messages": [
                    {
                        "text": "He's known as Captain Cool and won major ICC titles for India.",
                        "delay_ms": 0,
                    },
                    {
                        "text": "He is also known for wicketkeeping reflexes and finishing ability.",
                        "delay_ms": 5000,
                    },
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="Now I want a detailed one"))
    memory = wrapper.list_debug_view("s1")
    combined = " ".join(item["text"] for item in memory["planned_messages"])

    assert "more detailed overview" in combined.lower()
    assert "most successful captains" in combined.lower()
    assert memory["pending_messages"]


def test_fetch_style_request_preserves_structure_when_planner_overcompresses(tmp_path):
    core_text = (
        "The website belongs to Rishabh Bajpai.\n\n"
        "### About\n"
        "- Postdoctoral researcher at Washington University in St. Louis\n"
        "- Works on intelligent systems for motor disorders in children\n\n"
        "### Academic Background\n"
        "- PhD at IIT Delhi as a PMRF scholar\n"
        "- Focus on gait assessment and lower-limb motion\n\n"
        "### Projects\n"
        "1. Automated gait assessment\n"
        "2. Instrumented sock for foot kinematics\n"
        "3. Foot2hip low-cost kinematics system\n\n"
        "Source: https://www.rishabh-bajpai.com/"
    )
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=core_text,
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Fetch https://www.rishabh-bajpai.com/ and tell me what it says.",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner returned an over-short summary.",
                "messages": [
                    {
                        "text": "It is Rishabh Bajpai's site, and he works on motor-disorder research.",
                        "delay_ms": 0,
                    },
                ],
            },
        ],
    )

    response = wrapper.handle(
        ChatRequest(
            session_id="s1",
            message="Fetch https://www.rishabh-bajpai.com/ and tell me what it says.",
        )
    )
    memory = wrapper.list_debug_view("s1")
    combined = "\n".join(item["text"] for item in memory["planned_messages"])

    assert "### About" in combined
    assert "instrumented sock" in combined.lower()
    assert "https://www.rishabh-bajpai.com/" in combined
    assert response.metadata["pending_delivery_count"] >= 1


def test_dangling_number_marker_is_moved_to_next_message(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text="placeholder",
        planner_responses=[
            {
                "interrupt_type": "adapt_remaining_with_core",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Need fresh answer.",
                "message_for_core_agent": "4 aur btao",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner returned split marker.",
                "messages": [
                    {
                        "text": "Some explanation before the next item. **2.",
                        "delay_ms": 0,
                    },
                    {
                        "text": "Chapter 4, Verse 17 - On Action with Detachment.",
                        "delay_ms": 5000,
                    },
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="4 aur btao"))
    memory = wrapper.list_debug_view("s1")
    planned = memory["planned_messages"]

    assert not planned[0]["text"].rstrip().endswith("**2.")
    assert planned[1]["text"].startswith("**2. ")


def test_long_freeform_core_response_is_not_collapsed_to_intro_line(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=(
            "Of course! Here's a gentle, meaningful thing you could do today-"
            "something that's simple but can make a quiet difference in your day:\n\n"
            "Take 10 minutes to write down three small things you're grateful for-"
            "no matter how tiny they seem. "
            "It could be the way sunlight hits your window this morning, the smell of coffee brewing, "
            "or even just the fact that you got out of bed today. "
            "Then, if you feel up to it, share one of those things with someone-a friend, a family member, "
            "or even just text it to yourself as a reminder.\n\n"
            "It's not about grand gestures-just a little act of noticing what's good, even when life feels heavy. "
            "And who knows? That small moment might ripple into something bigger than you expect. "
            "You're already doing so much by showing up today."
        ),
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "tell me something to do today",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Only planned the intro, but the wrapper should preserve the whole answer.",
                "messages": [
                    {
                        "text": "Of course! Here's a gentle, meaningful thing you could do today-something that's simple but can make a quiet difference in your day:",
                        "delay_ms": 0,
                    }
                ],
            },
        ],
    )

    response = wrapper.handle(
        ChatRequest(session_id="s1", message="tell me something to do today")
    )
    memory = wrapper.list_debug_view("s1")

    assert response.messages[0].text.startswith(
        "Of course! Here's a gentle, meaningful thing you could do today-something that's simple but can make a quiet difference in your day:"
    )
    assert (
        "Take 10 minutes to write down three small things" in response.messages[0].text
    )
    assert response.metadata["pending_delivery_count"] >= 2
    assert any(
        "It could be the way sunlight hits your window this morning" in item["text"]
        for item in memory["pending_messages"]
    )
    assert all(len(item["text"]) <= 320 for item in memory["pending_messages"])
    assert memory["latest_core_response"].startswith(
        "Of course! Here's a gentle, meaningful thing you could do today"
    )


def test_wrapper_resplits_oversized_planner_message_into_small_chunks(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text=(
            "Making $10,000 per day is an ambitious and exciting goal-and definitely possible with the right strategy, mindset, and effort. "
            "Let's break it down realistically and explore some high-impact paths you could consider.\n\n"
            "### First: Is It Realistic?\n"
            "Yes-but not overnight. Many people achieve this through:\n"
            "- High-income skills\n"
            "- Scalable businesses\n"
            "- Investments or passive income streams\n\n"
            "### 1. Build a High-Income Skill & Freelance\n"
            "If you're skilled in software development, digital marketing, copywriting, or AI/ML engineering, you can charge premium rates.\n\n"
            "### 2. Start an Online Business\n"
            "Build something scalable like e-commerce, SaaS, or digital products.\n\n"
            "### 3. Content Creation & Monetization\n"
            "Build an audience and monetize through ads, sponsors, and products.\n\n"
            "### Action Steps\n"
            "1. Pick one path.\n"
            "2. Learn it deeply.\n"
            "3. Launch a small version.\n"
            "4. Scale based on results."
        ),
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "I want to make 10k per day",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner front-loaded too much into the first chunk.",
                "messages": [
                    {
                        "text": "Making $10,000 per day is an ambitious and exciting goal-and definitely possible with the right strategy, mindset, and effort. Let's break it down realistically and explore some high-impact paths you could consider. First: Is It Realistic? Yes-but not overnight. Many people achieve this through: - High-income skills - Scalable businesses - Investments or passive income streams Let's look at realistic ways to get close to that goal. 1. Build a High-Income Skill & Freelance If you're skilled in software development, digital marketing, copywriting, or AI/ML engineering, you can charge premium rates. 2. Start an Online Business Build something scalable like e-commerce, SaaS, or digital products. 3. Content Creation & Monetization Build an audience and monetize through ads, sponsors, and products.",
                        "delay_ms": 0,
                    },
                    {"text": "1. Pick one path.", "delay_ms": 5000},
                    {"text": "2. Learn it deeply.", "delay_ms": 5000},
                    {"text": "3. Launch a small version.", "delay_ms": 5000},
                    {"text": "4. Scale based on results.", "delay_ms": 5000},
                ],
            },
        ],
    )

    response = wrapper.handle(
        ChatRequest(session_id="s1", message="I want to make 10k per day")
    )
    memory = wrapper.list_debug_view("s1")

    assert len(response.messages[0].text) <= 320
    assert "2. Start an Online Business" not in response.messages[0].text
    assert memory["pending_messages"]
    assert all(len(item["text"]) <= 320 for item in memory["pending_messages"])
    assert response.metadata["pending_delivery_count"] > 4


def test_wrapper_assigns_default_delays_instead_of_planner_delays(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text="Raw core answer.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "hello",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Planner returned custom delays that should be normalized.",
                "messages": [
                    {"text": "First answer.", "delay_ms": 999},
                    {"text": "Second answer.", "delay_ms": 99999},
                    {"text": "Third answer.", "delay_ms": 12345},
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="hello"))
    memory = wrapper.list_debug_view("s1")

    assert memory["planned_messages"][0]["delay_ms"] == 0
    assert [item["delay_ms"] for item in memory["pending_messages"]] == [5000, 5000]


def test_topic_label_prefers_latest_user_message(tmp_path):
    wrapper, _, _, _ = _build_wrapper(
        tmp_path,
        core_text="This is a much longer assistant answer that should not drive the topic label.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "hello",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Single visible answer.",
                "messages": [
                    {"text": "A short answer.", "delay_ms": 0},
                ],
            },
        ],
    )

    wrapper.handle(
        ChatRequest(
            session_id="s1",
            message="Please help me map out a practical 30-day plan for learning Python and shipping one small project this month",
        )
    )
    memory = wrapper.list_debug_view("s1")

    assert memory["topic_label"] == (
        "Please help me map out a practical 30-day plan for learning Python and..."
    )


def test_wrapper_forwards_raw_user_message_to_core_agent(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="65",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Calculate 65 + 9 - 9",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Split into two messages.",
                "messages": [
                    {
                        "text": "Let's break it down: 65 plus 9 is 74, then subtracting 9 gives us 65.",
                        "delay_ms": 0,
                    },
                    {"text": "So the final answer is 65.", "delay_ms": 5000},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Same topic arithmetic follow-up.",
                "message_for_core_agent": "The user wants to add 4 to something, but no clear context is provided. Please clarify or provide additional details for processing.",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "Adding 4 to 65 gives 69.", "delay_ms": 0},
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="65+9-9?"))
    core_agent.response_text = "Adding 4 to 65 gives 69."

    response = wrapper.handle(ChatRequest(session_id="s1", message="add 4 to it"))

    assert len(core_agent.calls) == 2
    assert core_agent.calls[0].message == "65+9-9?"
    assert core_agent.calls[1].message == "add 4 to it"
    assert response.messages[0].text == "Adding 4 to 65 gives 69."


def test_wrapper_preserves_raw_pronoun_followups_across_multiple_turns(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'wrapper.db'}")
    history_provider = SQLAlchemyHistoryProvider(session_factory)

    class RecordingFakeCoreAgent:
        def __init__(self, response_text: str) -> None:
            self.response_text = response_text
            self.calls: list[ChatRequest] = []

        def respond(self, chat_request: ChatRequest) -> ChatResponse:
            self.calls.append(chat_request)
            history_provider.append_conversation_turn(
                chat_request.session_id,
                user_message=chat_request.message,
                assistant_message=self.response_text,
            )
            return ChatResponse(
                session_id=chat_request.session_id,
                response=self.response_text,
                metadata={"source": "fake_core"},
            )

    planner = FakePlanner(
        [
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "Calculate 65 + 9 - 9",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "65 + 9 - 9 equals 65.", "delay_ms": 0},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Continue the arithmetic thread.",
                "message_for_core_agent": "Clarify what to add 4 to.",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "Adding 4 to 65 gives 69.", "delay_ms": 0},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Continue the arithmetic thread.",
                "message_for_core_agent": "Clarify what to subtract 2 from.",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "Subtracting 2 from 69 gives 67.", "delay_ms": 0},
                ],
            },
            {
                "interrupt_type": "adapt_remaining",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": True,
                "reasoning": "Continue the arithmetic thread.",
                "message_for_core_agent": "Clarify what to multiply by 3.",
                "queue_action": "replace",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "Multiplying 67 by 3 gives 201.", "delay_ms": 0},
                ],
            },
        ]
    )
    core_agent = RecordingFakeCoreAgent(response_text="65")
    wrapper = ConversationWrapper(
        agent=core_agent,
        history_provider=history_provider,
        orchestration_agent=planner,
    )

    wrapper.handle(ChatRequest(session_id="s1", message="65+9-9?"))
    core_agent.response_text = "Adding 4 to 65 gives 69."
    second = wrapper.handle(ChatRequest(session_id="s1", message="add 4 to it"))
    core_agent.response_text = "Subtracting 2 from 69 gives 67."
    third = wrapper.handle(ChatRequest(session_id="s1", message="subtract 2 from that"))
    core_agent.response_text = "Multiplying 67 by 3 gives 201."
    fourth = wrapper.handle(
        ChatRequest(session_id="s1", message="now multiply it by 3")
    )
    history = history_provider.list_messages("s1")

    assert [call.message for call in core_agent.calls] == [
        "65+9-9?",
        "add 4 to it",
        "subtract 2 from that",
        "now multiply it by 3",
    ]
    assert second.messages[0].text == "Adding 4 to 65 gives 69."
    assert third.messages[0].text == "Subtracting 2 from 69 gives 67."
    assert fourth.messages[0].text == "Multiplying 67 by 3 gives 201."
    assert [item["text"] for item in history if item["role"] == "user"] == [
        "65+9-9?",
        "add 4 to it",
        "subtract 2 from that",
        "now multiply it by 3",
    ]


def test_ack_continue_without_pending_queue_still_calls_core_agent(tmp_path):
    wrapper, core_agent, _, _ = _build_wrapper(
        tmp_path,
        core_text="8 - 5 = 3.",
        planner_responses=[
            {
                "interrupt_type": "reset_and_new_query",
                "same_topic": False,
                "topic_continuity_confidence": 0.0,
                "use_core_agent": True,
                "reasoning": "Need the core agent.",
                "message_for_core_agent": "8-5",
                "queue_action": "replace",
                "clear_working_memory": True,
                "preserve_delivered_messages": False,
                "preserve_pending_messages": False,
            },
            {
                "reasoning": "Answer directly.",
                "messages": [
                    {"text": "8 - 5 = 3.", "delay_ms": 0},
                ],
            },
            {
                "interrupt_type": "ack_continue",
                "same_topic": True,
                "topic_continuity_confidence": 0.95,
                "use_core_agent": False,
                "reasoning": "Incorrectly treated as continue.",
                "message_for_core_agent": "",
                "queue_action": "preserve_pending",
                "clear_working_memory": False,
                "preserve_delivered_messages": True,
                "preserve_pending_messages": True,
            },
            {
                "reasoning": "Answer the arithmetic follow-up.",
                "messages": [
                    {"text": "3 + 59 = 62.", "delay_ms": 0},
                ],
            },
        ],
    )

    wrapper.handle(ChatRequest(session_id="s1", message="8-5"))
    core_agent.response_text = "3 + 59 = 62."

    response = wrapper.handle(ChatRequest(session_id="s1", message="+59"))
    memory = wrapper.list_debug_view("s1")

    assert len(core_agent.calls) == 2
    assert core_agent.calls[1].message == "+59"
    assert response.metadata["core_agent_called"] is True
    assert response.messages[0].text == "3 + 59 = 62."
    assert memory["latest_user_message"] == "+59"
    assert memory["pending_messages"] == []
