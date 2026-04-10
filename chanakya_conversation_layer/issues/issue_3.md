# Issue 3: Fix Broken Resume Flow Handoff to Core Agent

## Problem

There are real logic bugs in the resume flow that cause the wrapper to stop at an intermediate acknowledgement instead of actually continuing the conversation.

Two confirmed cases are broken:

### 1. Clarification / Rephrase Resume Never Hands Off to the Core Agent

In `app/services/resume_manager.py`, `_resume_clarified_answer()` returns:

- `act=PolicyAct.DIRECT_ANSWER`
- `response_spec=self.response_processor.single(user_message)`
- `metadata["handoff_to_core_agent"] = True`

However, in `app/services/conversation_wrapper.py`, the `resume_decision is not None` branch always realizes `resume_decision.response_spec` directly and returns it as the final response.

That means the `handoff_to_core_agent` flag is currently ignored.

Actual result:
- after the user clarifies or rephrases their request, the wrapper echoes the user's clarified text back as the final response

Expected result:
- after clarification or rephrase, control should pass to the core agent so the user receives an actual answer

### 2. Topic-Scoping Resume Stops at an Acknowledgement

In `app/services/resume_manager.py`, `_resume_topic_scope()` returns acknowledgement-only text such as:

- `"Got it. I'll keep it high level and focus on the main picture first."`
- `"Got it. I'll take the practical angle and focus on what to do with it."`

But `app/services/conversation_wrapper.py` sends that acknowledgement as the final response in the same generic `resume_decision is not None` branch.

Actual result:
- after the user chooses a topic angle or depth, the wrapper responds only with the acknowledgement
- the actual answer never follows

Expected result:
- after topic scoping, the system should proceed to the actual answer in the chosen style, not stop after acknowledgement

## Root Cause

`ConversationWrapper.handle()` treats all resume decisions as terminal realized responses.

It does not distinguish between:
- resume decisions that should produce a final user-visible response immediately
- resume decisions that should hand control back to the core agent after updating state/context

As a result, `ResumeDecision.metadata` contains control-flow information such as `handoff_to_core_agent`, but the wrapper does not actually use it.

## Affected Files

- `app/services/resume_manager.py`
- `app/services/conversation_wrapper.py`
- `tests/test_wrapper.py`

## Suggested Fix

Update the resume flow so `ConversationWrapper` can distinguish between:

1. **terminal resume responses**
   Resume manager returns a response that should be realized and sent directly.

2. **handoff resume responses**
   Resume manager updates working-memory state and/or acknowledges the user's selection, then passes control to the core agent for the actual answer.

Possible implementation directions:

- add an explicit field to `ResumeDecision` such as `handoff_to_core_agent: bool`
- or introduce a dedicated resume act/type for handoff behavior
- or make the wrapper honor the existing metadata flag in a clear and tested way

The fix should avoid overloading `response_spec` for cases where the real behavior is "continue to core agent now."

## Acceptance Criteria

1. **Clarification handoff works**: After a clarification or rephrase, the wrapper sends control to the core agent instead of echoing the user message back.
2. **Topic scope continuation works**: After the user selects a topic angle/depth, the system proceeds to the actual answer rather than stopping at acknowledgement text.
3. **Resume control flow is explicit**: Resume decisions clearly distinguish between terminal responses and handoff-to-agent behavior.
4. **No ignored handoff metadata**: If handoff metadata/flags exist, `ConversationWrapper` actually honors them.
5. **Tests cover both bugs**: Add tests for clarification/rephrase resume and topic-scoping resume so these regressions are caught.
6. **Existing resume behaviors still work**: Remaining-item disclosure, support flow, and interrupted-thread restoration continue to behave correctly.

## Related Issues

- `issues/issue_1.md` - Split conversation layer into an independent stack
- `issues/issue_2.md` - Build a developer debug dashboard for full conversation and agent trace visibility
- `issues/issue_4.md` - Replace hard-coded conversation orchestration with LLM-powered planning
