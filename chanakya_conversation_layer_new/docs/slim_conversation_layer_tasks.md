# Slim Conversation Layer Refactor Tasks

Please keep this file updated with new tasks, completed tasks, and any status changes as work progresses. And use "conda activate test" when required.

## Phase 1: Cut Dead Runtime Surface

### [X] Step 1: Remove Duplicate Legacy App Code

- [X] delete the unused legacy implementation under `app/services/`
- [X] delete old duplicate runtime modules in `app/routes.py`, `app/db.py`, `app/config.py`, `app/schemas.py`, `app/logging_utils.py`, and `app/evaluation.py`
- [X] keep only the minimal app entry surface needed to start Flask and serve templates

### [X] Step 2: Remove Non-Target Runtime Paths

- [X] remove `/chat/raw`
- [X] remove `/chat/post-processing-only`
- [X] remove related mode-comparison wiring
- [X] remove debug-only comparison assumptions from the runtime UI

### [X] Step 3: Remove Runtime Reporting and Review Features

- [X] remove evaluation/reporting routes from `core_agent_app/routes.py`
- [X] remove `core_agent_app/evaluation.py` from the runtime product surface
- [X] remove `core_agent_app/reporting.py` from the runtime product surface
- [X] remove report/review/experiment persistence models from runtime storage

## Phase 2: Simplify Core-Agent Boundary

### [X] Step 4: Clean the Core Agent Prompt and Adapters

- [X] remove conversation-layer instructions from `core_agent_app/services/core_agent.py`
- [X] remove `topic_scope_style` prompt rewriting
- [X] remove conversation-wrapper phrasing from the A2A adapter description
- [X] keep only core-agent-specific instructions

### [X] Step 5: Remove Demo/Hardcoded Behavior From Live Paths

- [X] remove `core_agent_app/services/demo_agent.py` from live usage
- [X] remove hardcoded behavioral branches from any production code path
- [X] ensure tests no longer depend on fake hardcoded intelligence in runtime code

### [X] Step 6: Expand Core-Agent Tools

- [X] review current tool surface in `core_agent_app/services/tools.py`
- [X] add backend-supported tools for web fetch
- [X] add backend-supported tools for web search
- [X] keep tool calling inside the core agent, not the conversation layer

## Phase 3: Replace the Conversation Layer

### [X] Step 7: Replace `ConversationWrapper` With a Slim Conversation Service

- [X] create a new slim service responsible only for current-response WM, routing, queueing, and delivery planning
- [X] remove the current policy/disclosure/resume/critique pipeline from the live path
- [X] keep the new service backend-owned and small

### [X] Step 8: Replace Current WM With Response-Scoped In-Memory State

- [X] remove persistent conversation-layer WM storage
- [X] define a minimal in-memory WM structure for the active response only
- [X] include only current-response queue, active delivery state, latest core response, and planner context
- [X] refresh WM for every new response

### [X] Step 9: Add WM-Manager LLM Call

- [X] define a structured output for WM management
- [X] let it decide whether to answer from WM or call the core agent
- [X] let it update queue state when interruptions happen
- [X] inject user conversation preferences through prompt context rather than DB-backed memory

### [X] Step 10: Add Human-Conversation Planner LLM Call

- [X] define a structured output for sequential human-style message chunks
- [X] support a first immediate message and later delayed messages
- [X] ensure the planner uses the injected user conversation preferences
- [X] ensure the planner is lightweight and does not overconsume tokens

### [X] Step 11: Remove Non-Target Conversation Modules

- [X] remove `conversation_layer/services/critique_pass.py` from the live path
- [X] remove `conversation_layer/services/disclosure_planner.py` from the live path
- [X] remove `conversation_layer/services/resume_manager.py` from the live path
- [X] remove `conversation_layer/services/episodic_summary.py` from the live path
- [X] remove `conversation_layer/services/user_profile.py` from the live path
- [X] remove `conversation_layer/services/secondary_flow_workflow.py` from the live path
- [X] remove orchestration-mode switching and related abstractions from the live path
- [X] remove `AgentCapabilities` and similar adapter-debug abstractions if they are no longer needed

## Phase 4: Backend-Owned Delivery Queue

### [X] Step 12: Implement In-Memory Sequential Delivery

- [X] maintain the pending assistant message queue in backend memory only
- [X] deliver one message at a time from the backend
- [X] wait 5 seconds before releasing the next queued message by default
- [X] remove each message from WM immediately after delivery

### [X] Step 13: Add Interruption Handling

- [X] detect when a new user message arrives before queued delivery completes
- [X] pause or discard remaining pending messages
- [X] rerun WM management with the new user message
- [X] replan the remaining human-style conversation accordingly

### [X] Step 14: Add Manual Pause Support

- [X] add a backend endpoint or action to pause next queued delivery
- [X] wire a simple UI button to it
- [X] make manual pause reuse the same internal pause state used by user interruptions

## Phase 5: History and Session Behavior

### [X] Step 15: Replace Raw Core Responses With Delivered Humanized Dialog

- [X] when handing history back to the core agent, ensure prior visible assistant turns reflect the conversation-layer-delivered messages
- [X] avoid keeping old raw core-agent responses as the visible assistant history
- [X] keep `session_id` only where the core agent needs it

### [X] Step 16: Keep A2A Support While Simplifying Modes

- [X] preserve local core-agent support
- [X] preserve A2A core-agent support
- [X] remove unnecessary mode matrix complexity while keeping both transports available

## Phase 6: UI and Startup Cleanup

### [X] Step 17: Simplify the Main UI

- [X] remove the debug dashboard
- [X] remove raw/post-processing comparison controls
- [X] show the active raw core-agent response for the current turn
- [X] show current WM values
- [X] show one scrollable list for core-agent chat history
- [X] add the manual pause button for next queued delivery

### [X] Step 18: Simplify App Startup

- [X] add one simple start script for the app
- [X] add one simple stop script if needed
- [X] keep A2A startup support clear and minimal

## Phase 7: Test Rewrite

### [X] Step 19: Replace Old Wrapper-Centric Tests

- [X] remove tests that lock in the old orchestration architecture
- [X] add tests for the three-call target path
- [X] add tests for queue delivery timing behavior
- [X] add tests for interruption replanning
- [X] add tests for manual pause
- [X] add tests for local and A2A core-agent paths

## Suggested Execution Order

1. cut dead runtime surface
2. clean core-agent prompt and tools
3. build the slim conversation service
4. add in-memory backend queue delivery
5. add interruption and manual pause
6. simplify UI
7. rewrite tests

## Next Phase: Transcript Regression Hardening

The next implementation phase is tracked in:

1. `docs/transcript_regression_hardening_plan.md`
2. `docs/transcript_regression_hardening_tasks.md`
