 # Transcript Regression Hardening Plan

 ## Goal

 Harden the slim conversation layer against real interruption and queue-management failures by converting observed transcripts into focused regression tests, then tightening wrapper behavior only where those tests expose ambiguity.

 This phase should avoid new architecture. The target is behavioral reliability in the current slim design.

 ## Why This Is Next

 The major simplification work is done. The current risk is no longer missing infrastructure; it is subtle state drift in interruption handling.

Recent bugs all came from small but important behavior edges:

1. preserving pending queues across acknowledgements
2. distinguishing same-topic constraints from topic resets
3. keeping structured multi-item responses intact through planning and delivery
4. ensuring only undelivered content is replaced during adaptation
 5. preventing planner truncation of long free-form core responses to a single intro line

 The fastest way to keep progress stable is to build a transcript-driven regression suite around those edges.

 ## Principles

 1. Prefer tests before refactors.
 2. Keep fixes local to `conversation_layer/services/conversation_wrapper.py` unless a broader change is clearly required.
 3. Preserve the slim three-call architecture.
 4. Keep the wrapper as the guardrail for queue correctness even when planner output is imperfect.
 5. Use real user transcripts over synthetic examples whenever possible.

 ## Scope

 Primary files:

 1. `tests/test_conversation_wrapper.py`
 2. `conversation_layer/services/conversation_wrapper.py`
 3. `conversation_layer/services/orchestration_agent.py` only if schema or prompt constraints must be tightened
 4. `docs/slim_conversation_layer_tasks.md`

 Optional UI follow-up file if we choose to reduce ambiguity with an explicit continue action:

 1. `app/templates/index.html`

 ## Workstreams

 ### Workstream 1: Build a Transcript-to-Test Regression Bucket

 Add focused tests that mirror real user interactions, especially short follow-ups during active queue delivery.

 Priority transcript families:

 1. acknowledgment-only follow-ups
 2. continue requests
 3. same-topic simplification requests
 4. constraints on the next pending item
 5. genuine topic pivots
 6. structured numbered-list delivery

 For each transcript-derived case, capture:

 1. initial user request
 2. core-agent raw response
 3. planner output(s)
 4. interruption message
 5. expected immediate visible response
 6. expected pending queue after handling
 7. expected delivered history
 8. expected `interrupt_type`

 ### Workstream 2: Make Queue Invariants Explicit in Tests

 Add assertions that make the intended model non-negotiable.

Required invariants:

1. already delivered messages are never re-delivered
2. `ack_continue` preserves the undelivered queue exactly
3. `adapt_remaining` replaces only future undelivered content
4. `adapt_remaining_with_core` preserves topic continuity while allowing a new core response
5. `reset_and_new_query` clears prior pending state only for genuine topic changes
6. structured numbered responses are not silently shortened by planner output
 7. long free-form core responses are not silently collapsed to a single intro sentence
 8. queue-cleared metadata matches the actual queue behavior

 ### Workstream 3: Harden Low-Information Follow-Up Classification

 Review and tighten behavior for short messages that are easy to misclassify.

 Priority user messages:

 1. `ok`
 2. `nice`
 3. `next`
 4. `continue`
 5. `go on`
 6. `tell me more`
 7. `short version`
 8. `not that one`

 Desired classification targets:

 1. passive acknowledgement with preserved queue
 2. explicit continue request with preserved queue
 3. same-topic adaptation without core call
 4. same-topic adaptation with core call
 5. genuine reset for new topic

 The latest user message should remain a hard constraint for same-topic replanning.

 ### Workstream 4: Add a Small State Transition Matrix

 Create compact tests that cover the main state combinations instead of only narrative transcripts.

 High-value matrix dimensions:

 1. pending queue exists or not
 2. manual pause requested or not
 3. same-topic or new-topic
 4. core call needed or not
 5. preserve pending or replace pending

 This matrix should catch regressions that are easy to miss when only testing long conversation stories.

 ### Workstream 5: Review Planner Dependence Boundaries

 Audit places where wrapper correctness currently relies too heavily on planner output.

 Wrapper guardrails should remain responsible for:

 1. preserving prior pending items for `ack_continue`
 2. expanding structured numbered content from the core response when planner output is incomplete
 3. preventing replay of delivered content
 4. ensuring queue metadata matches actual state transitions

 If planner prompt changes are needed, keep them minimal and support the wrapper rather than shifting correctness into prompt wording.

 ### Workstream 6: Optional UI Affordance Follow-Up

 After the regression suite is in place, consider adding an explicit continue control in the UI for paused or waiting queues.

 Goal:

 1. reduce heuristic interpretation of short user acknowledgements
 2. give the user a direct way to request the next pending item

 This should be a small product improvement, not a new control surface.

 ## Proposed Execution Order

 1. collect and normalize recent real transcripts into test scenarios
 2. add transcript-driven regression tests in `tests/test_conversation_wrapper.py`
 3. add queue invariant assertions and state-matrix tests
 4. make the smallest wrapper changes needed to satisfy failing tests
 5. run the focused test suite and confirm no behavioral regressions
 6. decide whether the UI needs a lightweight continue affordance

 ## Concrete Test Scenarios To Add

 1. `ok` after the first item of a queued explanation should preserve the remaining queue without new core output
 2. `next` after manual pause should resume pending delivery behavior rather than reset topic
 3. `tell me more` after a partial explanation should adapt only the undelivered portion
 4. `make the next one shorter` should modify only future pending content
 5. `do not use Switzerland in the next joke` should preserve delivered jokes and replace only the remaining joke
 6. `actually explain closures instead` should trigger `reset_and_new_query`
7. a 5-item numbered response should still produce 5 delivered items even if planner output is incomplete
8. an acknowledgement after a queue-preserving interruption should not lose the snapshot of pending items
 9. a long paragraph answer like `tell me something to do today` should not collapse into one introductory sentence with an empty pending queue

 ## Verification

 Minimum verification for this phase:

 1. run `pytest tests/test_conversation_wrapper.py`
 2. run the slim focused suite if wrapper behavior changes also affect history or A2A boundaries
 3. manually replay at least one real transcript that previously failed

 ## Expected Outcome

 At the end of this phase, the conversation layer should be boring in the best way:

 1. same-topic interruptions preserve continuity predictably
 2. pending queues survive acknowledgements correctly
 3. only undelivered content is adapted or replaced
 4. structured multi-item responses complete reliably
 5. future debugging becomes transcript-to-test work instead of architectural churn
