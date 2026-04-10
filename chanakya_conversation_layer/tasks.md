# Implementation Plan: Conversation Intelligence Layer

This plan is organized as verifiable feature milestones. Each step should produce a working slice of the system, not a partial piece of the final app with no way to test it.

## Product Direction

- Primary product target: reusable conversation-intelligence platform that can wrap different core agents.
- Primary app shape: dual-mode UI with a polished chat experience plus developer/debug inspection panels.
- Near-term delivery strategy: finish a strong MVP cleanly before expanding into broader adaptive or platform features.
- Current PRD-aligned milestone status: MVP feature plan complete through Step 13.

## Remaining Work Tracks

These tracks organize the unfinished PRD scope after the completed foundation in Steps 0-8.

### Track A: Conversation Memory

- Step 9: episodic session summary
- [X] Step 10: short-term user preference signals
- later: optional persistent preference/profile storage

### Track B: Flow Robustness

- Step 11: interruption handling and suspended threads
- Step 12: critique/revision pass before send

### Track C: Evaluation and Release Readiness

- Step 13: offline scenario harness, metrics, and MVP readiness checks

### Track D: Product Surface

- Keep the wrapper reusable independently from the Flask UI.
- Evolve the UI toward dual-mode behavior: chat-first user mode plus richer developer inspection mode.
- Make transcript, working memory, episodic summary, selected act, and runtime state easy to inspect.

## Project Baseline

- Frameworks: use Microsoft Agent Framework for the agent layer and Flask for the HTTP app.
- Environment: use a Conda environment named `test`.
- Suggested daily startup command:

```bash
conda activate test
```

- Suggested app entry validation command shape:

```bash
conda activate test && flask --app app run
```

## [x] Step 0: Bootstrap a Simple Chat Agent with Tool and Persistent Memory

Feature goal: stand up the smallest useful system before adding conversation intelligence.

Implementation:

- [X] Create the Conda-based Python project scaffold and dependency setup for Microsoft Agent Framework and Flask.
- [X] Create a minimal Flask app with a health route and one chat route.
- [X] Build a simple Microsoft Agent Framework chat agent using the Python `ChatAgent` or `Agent` pattern.
- [X] Add one simple tool, such as `get_time` or `get_weather_mock`, so the agent can demonstrate tool usage.
- [X] Add persistent session memory so a user can say their name in one turn and the agent can recall it in the next turn.
- [X] Store memory by `session_id`, not only in request-local state.
- [X] Add a basic test for the Flask route and one test for memory recall.

Validation:

- [X] Start the app from the `test` Conda environment.
- [X] Send: "My name is Rishabh."
- [X] Send again with the same `session_id`: "What's my name?"
- [X] Ask a tool-backed question such as: "What time is it?"

Expected outcome:

- [X] Flask app starts successfully.
- [X] `/health` returns `200`.
- [X] The agent remembers the user's name across requests for the same `session_id`.
- [X] The tool executes and the response clearly reflects tool output.

Acceptance check:

- [X] `conda activate test && pytest`
- [X] `conda activate test && flask --app app run`

## [x] Step 1: Add a Wrapper Layer Around the Core Agent

Feature goal: separate the base agent from the future conversation-intelligence logic.

Implementation:

- [X] Introduce a conversation wrapper service between Flask and the core Microsoft agent.
- [X] Define request and response contracts for the wrapper.
- [X] Ensure the wrapper can forward a user message to the base agent and return the result unchanged for now.
- [X] Add structured logging for session id, user message, wrapper stage, and final response.
- [X] Keep the core agent swappable behind an adapter interface.

Validation:

- [X] Send a normal message through the Flask chat endpoint.
- [X] Confirm the wrapper logs each stage without changing the answer.

Expected outcome:

- [X] The wrapper exists as a separate module.
- [X] The base behavior still works exactly as before.
- [X] Logs show the message passed through the wrapper pipeline.

Acceptance check:

- [X] Chat output matches the Step 0 behavior.
- [X] Wrapper logs are visible for each request.

## [x] Step 2: Add Transcript Storage and Session State

Feature goal: preserve exact conversation turns independently from agent memory.

Implementation:

- [X] Store raw user and assistant turns in transcript storage keyed by `session_id`.
- [X] Add utilities to fetch recent transcript history.
- [X] Keep transcript storage separate from the core agent's own internal memory representation.
- [X] Add APIs or debug helpers to inspect transcript history during development.

Validation:

- [X] Have a three-turn conversation in one session.
- [X] Inspect stored transcript entries.

Expected outcome:

- [X] Every turn is stored in order with speaker labels and timestamps.
- [X] Transcript retrieval for a session returns the full recent conversation.

Acceptance check:

- [X] A test verifies that three requests create three user turns and three assistant turns in storage.

## [x] Step 3: Add Working Memory Schema for Undisclosed State

Feature goal: explicitly track what the system knows but has not said yet.

Implementation:

- [X] Implement the PRD working-memory schema as structured objects.
- [X] Add fields for `dialogue_phase`, `known_but_undisclosed`, `pending_questions`, `conversation_plan`, `runtime_state`, and `expiry_policy`.
- [X] Create per-session read and update operations for working memory.
- [X] Add cleanup rules for stale working-memory entries.
- [X] Add debug output to inspect working memory after each turn.

Validation:

- [X] Simulate a turn that produces two undisclosed items and one pending question.
- [X] Inspect working memory after the turn.

Expected outcome:

- [X] Working memory stores structured state outside the visible transcript.
- [X] The system can show what is known, what is undisclosed, and what the next planned step is.

Acceptance check:

- [X] A test asserts that undisclosed items and pending questions are persisted correctly by session.

## [x] Step 4: Implement a Rule-Based Dialogue Policy Engine

Feature goal: decide the next conversational act before generating the final response.

Implementation:

- [X] Create a rule-based policy engine for MVP.
- [X] Support core acts such as `DIRECT_ANSWER`, `ASK_CLARIFICATION`, `ASK_PREFERENCE`, `DISCLOSE_ONE_ITEM`, `SUMMARIZE_THEN_PAUSE`, `YIELD_TO_USER`, and `CLOSE`.
- [X] Encode PRD rules for simple answers, mixed-valence information, ambiguity, and user-preference prompts.
- [X] Add policy reasoning output so developers can inspect why an act was selected.

Validation:

- [X] Run scenario tests for a simple factual question, an ambiguous question, and a mixed good/bad news case.

Expected outcome:

- [X] Simple questions select `DIRECT_ANSWER`.
- [X] Ambiguous requests select `ASK_CLARIFICATION` or `ASK_PREFERENCE`.
- [X] Mixed good/bad news does not dump all information at once.

Acceptance check:

- [X] Policy tests pass for all defined scenarios.

## [x] Step 5: Add Disclosure Planning and One-Item-At-A-Time Delivery

Feature goal: reveal information gradually instead of dumping everything.

Implementation:

- [X] Build a disclosure planner on top of working memory.
- [X] Add prioritization for undisclosed items.
- [X] Track which facts are retrieved versus revealed.
- [X] Implement logic for asking the user which item they want first when multiple items compete.
- [X] Preserve unrevealed items after the current turn ends.

Validation:

- [X] Use a scenario with one positive and one negative update.
- [X] Ask the system for updates.

Expected outcome:

- [X] The system first asks which category or update the user wants.
- [X] Only the selected item is disclosed next.
- [X] Remaining items stay in working memory for later turns.

Acceptance check:

- [X] A test verifies that only one item is disclosed while the others remain unrevealed in working memory.

## [x] Step 6: Add a Response Realizer for Natural User-Facing Turns

Feature goal: convert chosen acts into natural language without leaking hidden state.

Implementation:

- [X] Create a response realizer that accepts the selected act and the current state.
- [X] Add realization patterns for direct answers, preference questions, summaries, and one-item disclosures.
- [X] Ensure the response aligns with what has already been said in the transcript.
- [X] Prevent the realizer from mentioning unrevealed items unless the policy allows it.

Validation:

- [X] Replay Step 5 scenarios through the realizer.
- [X] Inspect generated user-facing text.

Expected outcome:

- [X] Messages sound conversational and concise.
- [X] A preference question sounds natural, such as asking whether the user wants good news or bad news first.
- [X] The wording does not reveal all hidden items prematurely.

Acceptance check:

- [X] Snapshot or assertion-based tests pass for act-to-response generation.

## [x] Step 7: Add Yield/Resume Conversation Flow

Feature goal: support true multi-turn pacing with resumable state.

Implementation:

- [X] Add a simple state machine or graph runtime.
- [X] Allow the system to yield after asking a preference question.
- [X] Save `resume_from` and other runtime state in working memory.
- [X] Resume the correct plan when the user responds in the same session.

Validation:

- [X] Ask for updates.
- [X] Let the system ask which item to show first.
- [X] Reply: "Bad news first."

Expected outcome:

- [X] The system pauses at a natural handoff.
- [X] The next user message resumes the saved plan instead of starting over.
- [X] The requested item is disclosed next.

Acceptance check:

- [X] An integration test verifies that a paused session resumes from saved state and not from scratch.

## [x] Step 8: Add Async Filler for Slow Tool Calls

Feature goal: make the assistant feel present while waiting on tools or longer agent work.

Implementation:

- [X] Add optional filler responses before slow operations.
- [X] Keep filler short and context-appropriate.
- [X] Skip filler when the answer is fast or trivial.
- [X] Ensure filler does not alter disclosure state or transcript semantics incorrectly.

Validation:

- [X] Simulate a delayed tool call.
- [X] Observe first response timing and final answer flow.

Expected outcome:

- [X] The user first gets a brief acknowledgement like "Let me check that."
- [X] The final answer arrives after the tool completes.
- [X] Filler does not replace the real answer.

Acceptance check:

- [X] A test verifies filler appears for delayed calls and is absent for immediate calls.

## [x] Step 9: Add Session-Level Episodic Summary

Feature goal: compress the running conversation without losing the interaction trajectory.

Implementation:

- [X] Add a session-level episodic summary store that is separate from transcript and working memory.
- [X] Build deterministic summary updates from transcript plus working memory.
- [X] Track what has already happened, what was disclosed, what remains pending, and the current resume state.
- [X] Update the summary only at controlled turn boundaries, not every token or message chunk.
- [X] Surface episodic summary in the developer side of the UI and debug endpoints.
- [X] Prepare the summary to later support prompt compression and resumable dialogue continuity.

Validation:

- [X] Run a multi-turn conversation with at least five turns.
- [X] Inspect the generated session summary.
- [X] Verify that a mixed-update conversation records user choice, disclosed item, and remaining pending item.

Expected outcome:

- [X] The summary captures major events, user choices, and still-undisclosed items.
- [X] The agent can continue the conversation using the summary plus recent transcript.
- [X] Developers can inspect summary state independently from transcript and working memory.

Acceptance check:

- [X] A test verifies the summary contains prior decision points and pending conversational state.

## [x] Step 10: Add User Preference Signals for Delivery Style

Feature goal: adapt how the agent responds during the session.

Implementation:

- [X] Infer short-term signals such as concise vs detailed, direct vs gradual, and options-first vs explanation-first.
- [X] Update signals after each user turn.
- [X] Feed these signals into policy decisions and response realization.
- [X] Keep the adaptation session-scoped for MVP.
- [X] Make preference signals visible in the developer UI so they can be audited during testing.

Validation:

- [X] In one session, use phrases like "short version" or "just give it to me directly."
- [X] In another session, ask for guided or step-by-step delivery.

Expected outcome:

- [X] The policy shifts behavior according to the user's style.
- [X] Concise users get faster direct answers when appropriate.
- [X] Interactive users get more guided pacing.

Acceptance check:

- [X] Tests verify that identical scenarios can select different acts when preference signals differ.

## [x] Step 11: Add Interruption Handling and Suspended Threads

Feature goal: handle topic switches without losing unfinished state.

Implementation:

- [X] Detect when the user changes topics mid-flow.
- [X] Move the unfinished plan into suspended state.
- [X] Track active versus suspended conversation threads.
- [X] Allow later recovery of the suspended plan when relevant.
- [X] Preserve episodic summary integrity when active and suspended threads diverge.

Validation:

- [X] Start a multi-turn update flow.
- [X] Before finishing it, ask an unrelated question.
- [X] Later ask to continue the earlier topic.

Expected outcome:

- [X] The unrelated question is answered without corrupting the original plan.
- [X] The earlier conversation can be resumed with preserved undisclosed items.

Acceptance check:

- [X] An integration test verifies suspended and active thread states remain separate.

## [x] Step 12: Add Critique Pass Before Sending Responses

Feature goal: catch robotic or over-disclosing responses before they reach the user.

Implementation:

- [X] Add a lightweight critique step before final response output.
- [X] Check whether the draft says too much, discloses too early, or breaks dialogue-phase coherence.
- [X] Revise the response when the draft violates policy rules.
- [X] Log critique decisions for inspection.
- [X] Show critique outcomes in developer mode without exposing hidden reasoning in user mode.

Validation:

- [X] Feed in a draft response that dumps multiple sensitive items.
- [X] Run the critique pass.

Expected outcome:

- [X] The draft is revised into a more appropriate next step, such as a preference question or one-item disclosure.
- [X] Critique stays lightweight and does not block normal direct answers.

Acceptance check:

- [X] Tests verify the critique catches over-disclosure scenarios and leaves acceptable drafts unchanged.

## [x] Step 13: Add Evaluation Harness and MVP Readiness Checks

Feature goal: make the system measurable and shippable as an MVP.

Implementation:

- [X] Build an offline scenario suite for simple Q&A, mixed good/bad news, ambiguous requests, sensitive information, preference-driven pacing, and interrupted flows.
- [X] Add automated checks for act selection, undisclosed-item tracking, and resume correctness.
- [X] Add debug endpoints or logs for inspecting transcript, working memory, episodic summary, selected act, and final response.
- [X] Document the supported behavior and known MVP limits.

Validation:

- [X] Run the full evaluation suite from the `test` Conda environment.
- [X] Review logs for at least one scenario in each category.

Expected outcome:

- [X] The system passes the defined scenario suite.
- [X] Developers can inspect why a response happened and what state produced it.
- [X] The MVP is ready for iterative improvement instead of guesswork.

Acceptance check:

- [X] `conda activate test && pytest`
- [X] Scenario reports show pass/fail by feature area.

## MVP Exit Criteria

The MVP is complete when all of the following are true:

- [X] Flask serves the chat API from the `test` Conda environment.
- [X] A Microsoft Agent Framework-based core agent runs with at least one tool.
- [X] Persistent memory works by `session_id`.
- [X] Transcript, working memory, and episodic summary are stored separately.
- [X] The policy engine selects acts before response generation.
- [X] The disclosure planner can hold back unrevealed items.
- [X] Yield/resume works across turns.
- [X] Filler appears only when tool latency warrants it.
- [X] Interruption handling preserves unfinished state.
- [X] The dual-mode UI can expose developer state without cluttering the core chat experience.
- [X] The wrapper remains reusable independently from the Flask demo UI.
- [X] Every feature above has at least one passing acceptance test.
