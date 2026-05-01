# Prompt Optimization Suggestions

This note reviews the current orchestration prompts in `conversation_layer/services/conversation_wrapper.py` and suggests optimizations that should improve reliability or reduce prompt tokens without adding any extra model calls.

## Scope

- Working-memory manager prompt: `conversation_wrapper.py:851-860`
- Delivery planner prompt: `conversation_wrapper.py:1341-1374`

These suggestions are intentionally limited to prompt and payload shaping. They do not add retries, extra validation passes, or additional inference steps.

## Main Findings

1. The delivery planner prompt is doing the right job, but it repeats the same constraints in several different phrasings.
2. The working-memory manager prompt is already small, but it can be made more decision-oriented and less descriptive.
3. The delivery planner payload already contains strong structure. Because of that, some prompt text can be shortened without losing meaning.
4. The highest-value optimization is not more instructions. It is sharper, less redundant instructions with clearer priority ordering.

## Recommended Principles

1. Put hard constraints first.
2. Remove repeated restatements of the same idea.
3. Use compact task framing like "segment" and "preserve" instead of long explanatory sentences.
4. Let the payload carry structure and context; let the prompt carry rules and priorities.
5. Keep retry-only wording out of the base prompt.

## Working-Memory Prompt

### Current Issues

1. It explains the role of the memory well, but the decision rules can be stated more directly.
2. It spends too many tokens describing `adapt_remaining_with_core` in prose.
3. It does not explicitly say to prefer the smallest intervention that satisfies the user message.

### Suggested Rewrite

```text
You are the working-memory manager for a thin conversation delivery layer.
The memory belongs to the current topic and should persist across same-topic follow-ups.

Classify the latest user message as exactly one of:
- ack_continue
- adapt_remaining
- adapt_remaining_with_core
- reset_and_new_query

Decision rules:
- ack_continue: the user is asking to continue the current queued answer; usually preserve the queue and avoid a core-agent call.
- adapt_remaining: keep delivered content, keep the same topic, and adjust only future undelivered content using the user's latest message.
- adapt_remaining_with_core: keep delivered content, keep the same topic, and call the core agent for new information that should replace or refine only the remaining undelivered content.
- reset_and_new_query: clear active-topic working memory and start a new topic.

Prefer the smallest intervention that satisfies the user's latest message.
Return only valid JSON.
```

### Why This Should Help

1. Fewer tokens.
2. Clearer class boundaries.
3. Better bias toward preserving the queue when possible.

## Delivery Planner Prompt

### Current Issues

1. The prompt repeats completeness constraints in multiple ways:
   - full ordered set
   - complete delivery plan
   - do not summarize or omit
   - preserve atomic units
   - do not stop after the opening part
2. It repeats voice-boundary constraints in multiple ways:
   - natural spoken pause
   - one spoken beat per message
   - do not merge long points
   - final boundaries are delivered as-is
3. The adaptation-mode rules are useful, but they are verbose.
4. The prompt includes both style guidance and anti-style guidance in long prose, which increases token cost and can diffuse the priority order.

### Suggested Rewrite

```text
You are the delivery planner for a thin conversation layer.

Task:
Convert the latest core response into the full ordered set of assistant messages to deliver for this turn.

Priority rules:
1. The core response is the authoritative source content for this turn.
2. Your job is voice-friendly delivery planning, not answer generation.
3. Preserve the substance, scope, and count of the core response unless the latest user message explicitly asks for a shorter or partial answer.
4. Return the complete delivery plan for this turn, not just the first message.
5. Never repeat or restart content already present in delivered_summary or delivered_messages.

Segmentation rules:
- Split at natural spoken pause points.
- Usually deliver one atomic idea per message.
- Do not merge multiple long points into one message.
- Do not split in the middle of a thought.
- Messages should usually be short, conversational, and at most 1 to 3 sentences.

Adaptation rules:
- ack_continue: usually preserve the remaining queue and avoid restarting.
- adapt_remaining: adjust only future undelivered content using the latest user message.
- adapt_remaining_with_core: preserve delivered content and use the fresh core response to replace or refine only the remaining undelivered content.
- reset_and_new_query: ignore the previous topic and start fresh.

Style rules:
- Follow conversation_tone_instruction for delivery tone.
- Follow tts_instruction for speech formatting.
- Light rewording for speech is allowed, but do not change meaning or reduce coverage.
- Avoid generic wrap-up lines unless the user explicitly asked for broader help or next steps.

Return only user-visible assistant messages as valid JSON.
Set coverage_assertion="complete" only if the delivery plan fully covers everything that should be delivered for this turn.
```

### Why This Should Help

1. Lower token count.
2. Better instruction hierarchy.
3. Less chance that the model treats repetition as optional nuance instead of hard constraints.
4. Easier to maintain as the product evolves.

## Specific Micro-Optimizations

### 1. Remove duplicated completeness language

Current delivery prompt says all of these in different ways:

- produce the full ordered set
- produce the complete delivery plan
- do not summarize or omit
- preserve all distinct atomic informational units
- do not stop after only the opening part

Recommendation:
- keep one strong completeness block
- keep one strong segmentation block

### 2. Shorten the atomic-unit definition

Current:

```text
An atomic unit may be a step, reason, warning, example, recommendation, list item, or one short paragraph expressing a single idea.
```

Recommendation:

```text
Usually deliver one atomic idea per message, such as one step, reason, example, warning, list item, or short paragraph.
```

This says the same thing with fewer tokens and keeps the rule near segmentation.

### 3. Compress interruption wording

Current interruption line is descriptive and long.
Recommendation: make it shorter and more operational.

Suggested version:

```text
If the user interrupted while messages were pending, make the next messages responsive to the interruption while staying grounded in the latest core response.
```

### 4. Keep tone and TTS fields explicit in the prompt

These two lines are worth keeping even in a shorter prompt:

- follow `conversation_tone_instruction`
- follow `tts_instruction`

They are short, high-value, and clearly tied to host-app control.

### 5. Consider shrinking `reasoning` expectations

If internal reasoning is not used downstream, the output schema could eventually reduce `reasoning` to an optional short string or remove it entirely. That would save both prompt and completion tokens. This is the only suggestion here that would require a contract change, so it should be treated separately.

## Suggested Priority Order For Future Changes

1. First, shorten and reorder the delivery planner prompt.
2. Second, simplify the working-memory manager prompt.
3. Third, consider whether `reasoning` is worth its token cost.

## Recommendation Summary

The prompts can be optimized safely.

Best immediate change:
- rewrite the delivery planner prompt into shorter priority blocks without changing behavior.

Expected effect:
- same number of model calls
- slightly lower prompt token usage
- clearer instruction hierarchy
- likely better first-pass adherence on completeness and pause boundaries
