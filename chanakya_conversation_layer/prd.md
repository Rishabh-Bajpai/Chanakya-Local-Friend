
# PRD: Conversation Intelligence Layer (CIL) for Human-Like Agents

## 1. Product Name

**Conversation Intelligence Layer (CIL)**

## 2. Summary

* [ ] Build a modular conversation layer that sits between users and an existing agent, making the agent behave more like a thoughtful human conversational partner. The layer should manage pacing, disclosure, turn strategy, and short-lived working memory so the system does not always dump the full answer in one step. It should decide what to say now, what to hold back, when to ask a preference question, and when to fetch more information from the core agood dit iegent.

The layer must be reusable across different agents without changing their internal tool use or task logic.

---

## 3. Problem Statement

Current agents are optimized to complete tasks in a single response. This often produces unnatural conversations:

- they reveal all information immediately
- they do not ask preference questions before disclosing mixed or sensitive information
- they do not manage pacing well
- they do not separate “what is known” from “what should be said now”
- their “working memory” is usually implicit inside one model call rather than explicitly managed

Humans do not just compute answers. They manage the interaction. They decide how much to say, in what order, and whether to pause for user input before continuing.

We need a reusable layer that adds this conversational intelligence to any agent.

---

## 4. Goal

Create a reusable layer that improves conversational naturalness by introducing:

- explicit working memory
- dialogue policy
- gradual disclosure
- turn-by-turn response planning
- adaptation to user conversational preferences
- stateful turn-taking across pauses, interruptions, and resumptions

The system should preserve the core agent’s ability to use tools and retrieve facts, while improving how information is delivered.

---

## 5. Non-Goals

This product will not:

- replace the core agent’s reasoning or tool stack
- attempt full AGI-style cognitive architecture
- permanently store all transient thoughts
- simulate emotions deceptively
- generate long hidden chains of thought for logging or display
- guarantee human indistinguishability in all conversation settings

---

## 6. Target Users

### Primary

- developers building assistants, copilots, and customer-facing chatbots
- teams with an existing agent that feels too robotic

### Secondary

- research teams experimenting with memory architectures
- enterprise platforms that want a standard conversation wrapper around multiple agents

---

## 7. User Needs

Developers need a system that:

- can wrap around existing agents
- improves pacing without rewriting the core agent
- can ask follow-up questions before revealing everything
- stores short-lived conversational state outside the visible transcript
- can decide whether to answer directly or interact gradually
- can pause and resume naturally across turns
- is inspectable and debuggable

End users need an assistant that:

- feels more natural
- does not overwhelm them
- adapts to how they want information delivered
- handles mixed good/bad news more gracefully
- remembers what has and has not been said in the current dialogue
- feels present while tools are running

---

## 8. Core Insight

The key product principle is:

**Knowing something and saying it are different actions.**

The system must explicitly separate:

- **knowledge state**: facts the system has
- **disclosure policy**: what should be revealed now
- **utterance**: the actual user-facing wording

---

## 9. Proposed Solution

Build a **Conversation Intelligence Layer** with three primary modules:

### A. Dialogue Policy Manager

Decides how the current turn should unfold.

Examples:

- answer directly
- ask a preference question
- reveal one item only
- summarize first, then elaborate
- ask clarification before tool use
- deliver sensitive information gradually
- yield back to the user when a natural pause is reached

### B. Working Memory Store

Holds short-lived, structured conversational state not shown in the main chat transcript.

Examples:

- known but undisclosed facts
- current dialogue phase
- pending questions
- user’s short-term conversational cues
- next-step plan
- what the system is waiting on
- partially completed response plans
- resumable state after a pause or interruption

### C. Response Realizer

Converts the selected next conversational act into natural language.

Examples:

- “I found two updates—want the good one or the annoying one first?”
- “There are a couple of parts to this. I’ll start with the headline.”
- “I can go deeper, but here’s the short version first.”
- “Let me pull up the latest feeds for you...”

---

## 10. Product Architecture

### 10.1 High-Level Flow

1. User sends a message
2. Layer interprets intent and current dialogue state
3. Layer decides whether it already has enough information
4. If not, it may emit an immediate conversational filler while triggering the core agent or tool
5. Tool or core agent results are written into graph state / working memory
6. Dialogue policy decides what to reveal now
7. Response realizer generates one user-facing turn
8. State machine either yields to the user or continues if appropriate
9. Working memory is updated after user response or interruption

### 10.2 System Components

- User Interface
- Conversation Intelligence Layer
  - intent interpreter
  - dialogue policy engine
  - working memory manager
  - disclosure planner
  - response realizer
  - critique/revision pass
  - state machine / graph runtime
- Core Agent
  - tools
  - RAG/search
  - task planning
  - action execution
- Long-term memory/profile system
- Transcript/STM store

---

## 11. Memory Model

### 11.1 Transcript Memory

Raw recent turns between user and system.

Purpose:

- preserve exact wording
- maintain recent local context

### 11.2 Episodic Session Memory

Compressed summary of the current conversation.

Purpose:

- track what has happened
- reduce prompt size
- preserve interaction trajectory

Example:

- user asked for latest news
- system found one positive and one negative story
- system asked user which they wanted first
- user chose bad news first
- positive item still undisclosed

### 11.3 Working Memory

Short-lived editable state for the current and next few turns.

Purpose:

- manage active dialogue
- track undisclosed information
- hold current plan and pending actions
- maintain resumable state between yields

### 11.4 Long-Term Memory

Persistent profile and stable preferences.

Examples:

- user prefers concise answers
- user likes options before explanation
- user dislikes too many clarifying questions

---

## 12. Working Memory Schema

Initial version should use structured objects instead of free-form notes.

### Example Schema

```json
{
  "dialogue_phase": "pre_disclosure",
  "conversation_mode": "guided",
  "user_preference_signals": {
    "verbosity": "medium",
    "directness": "medium",
    "delivery_style": "incremental"
  },
  "known_but_undisclosed": [
    {
      "id": "item_1",
      "type": "news",
      "valence": "positive",
      "summary": "Positive story summary",
      "detail_ref": "core_agent_result_1",
      "priority": 0.9
    },
    {
      "id": "item_2",
      "type": "news",
      "valence": "negative",
      "summary": "Negative story summary",
      "detail_ref": "core_agent_result_2",
      "priority": 0.8
    }
  ],
  "pending_questions": [
    "Which item does the user want first?"
  ],
  "conversation_plan": [
    "ask order preference",
    "disclose selected item",
    "check whether user wants second item",
    "offer actionable details"
  ],
  "tool_state": {
    "fresh_info_available": true
  },
  "runtime_state": {
    "yielded_to_user": true,
    "resume_from": "disclose_selected_item"
  },
  "expiry_policy": {
    "max_turns": 5
  }
}
```

---

## 13. Functional Requirements

### 13.1 Dialogue Policy

The system must decide the next conversational act before generating the final text.

Supported initial dialogue acts:

- `DIRECT_ANSWER`
- `ASK_CLARIFICATION`
- `ASK_PREFERENCE`
- `ACKNOWLEDGE`
- `DISCLOSE_ONE_ITEM`
- `SUMMARIZE_THEN_PAUSE`
- `OFFER_OPTIONS`
- `CHECK_READINESS`
- `YIELD_TO_USER`
- `REPAIR`
- `CLOSE`

### 13.2 Disclosure Planning

The system must:

- distinguish between retrieved facts and revealed facts
- support gradual multi-turn disclosure
- prioritize which fact or step to reveal next
- ask user preference when order matters
- preserve unrevealed items across yields and resumptions

### 13.3 Core Agent Interaction

The layer must be able to ask the core agent for:

- facts
- tool results
- summaries
- options
- recommendations
- risks
- action outcomes

The layer should prefer structured returns over pre-written final prose where possible.

### 13.4 Working Memory Lifecycle

The system must:

- create WM entries during each turn
- update WM after each user reply
- decay or remove stale items
- avoid polluting long-term memory with transient planning state
- save resumable state when dialogue yields control back to the user

### 13.5 User Adaptation

The system should infer and update short-term user style preferences such as:

- concise vs detailed
- direct vs gradual
- options first vs explanation first
- tolerance for back-and-forth

### 13.6 Critique Pass

Before sending a message, the layer should run a lightweight critique:

- Am I saying too much at once?
- Should I ask before disclosing?
- Is the order socially natural?
- Is this turn coherent with current dialogue phase?

If needed, revise the response.

### 13.7 Async Presence / Filler

The system should be able to emit low-latency filler when a tool call or slower reasoning step is required.

Examples:

- “Let me pull that up.”
- “Give me a second—I’m checking the latest updates.”
- “Let me pull up the latest feeds for you...”

This filler must be optional, brief, and context-appropriate.

### 13.8 State Machine / Graph Execution

The system must:

- support explicit node execution over a graph or state machine
- allow yield points where control returns to the user
- resume from saved state on the next turn
- preserve partially executed plans without losing context

### 13.9 Interruption Handling

The system should:

- support context switches while preserving prior unfinished state
- recover earlier plans after interruptions when relevant
- maintain separate active and suspended thread state where necessary

---

## 14. Policy Rules for MVP

Start with rule-based policy before learned policy.

### Direct answer when:

- request is low stakes
- answer is simple
- user asked for speed or brevity
- no mixed-valence or sensitive content exists

### Gradual disclosure when:

- there is mixed good/bad news
- information is sensitive or emotionally loaded
- multiple items compete for priority
- a preference question would improve experience
- dumping all details would overwhelm the user

### Ask preference when:

- ordering matters
- there are multiple branches
- the user’s intent is under-specified
- tone or depth should be chosen by the user

### Use filler when:

- a tool call is needed
- response latency may otherwise feel dead
- a brief acknowledgement would improve perceived presence

### Yield when:

- a natural handoff point is reached
- a preference question has been asked
- enough information has been given for the user to steer next
- continuing would feel like over-disclosure

---

## 15. Example Behavior

### 15.1 Current Agent Behavior

**User:** “Tell me the latest updates.”

**Agent:**
“Here are two updates: your promotion was approved, but your transfer request was denied. Also, finance needs your reimbursement form by Friday.”

### 15.2 Desired Behavior

**System:**
“I found a few updates. One is good news, one is more annoying, and one is just admin. Which do you want first?”

**User:**
“Bad news first.”

**System:**
“Your transfer request was denied. Want the reason, or do you want me to give you the other updates first?”

This is the target interaction style.

### 15.3 Example Execution Flow: The News Scenario

**User:** “Tell me the news.”

**Agent (WM):** “User wants news. I need to fetch it.” → Triggers `News_Tool`.

**Agent (Async):** Streams immediate filler: “Let me pull up the latest feeds for you...”

**News_Tool:** Returns 5 articles (3 tech, 2 market) to the graph state.

**Agent (WM):** “I have 5 articles. Too many to read at once. Plan: 1. Categorize. 2. Ask for preference. 3. Yield.”

**Agent (Spoken):** “I found a few updates. There's some interesting tech news and a couple of market reports. Which would you like to dive into first?”

**State Machine:** Executes `YIELD_TO_USER`. Graph pauses. State is saved.

**User:** “Tech, please.”

**State Machine:** Resumes. Reads saved state containing the 3 tech articles.

**Agent (Spoken):** Delivers the first tech article naturally.

This is the reference execution pattern for the MVP.

---

## 16. MVP Scope

### Included

- wrapper around existing agent
- explicit WM state
- dialogue acts
- disclosure queue
- rule-based policy engine
- response realization
- critique pass
- session-level episodic summary
- graph/state-machine execution with yield/resume
- basic filler generation for perceived presence

### Excluded

- full reinforcement learning policy optimization
- affective avatar behavior
- autonomous long-horizon planning
- rich multimodal interaction policy
- deep persistent personalization

---

## 17. UX Principles

- do not slow-roll trivial answers
- do not ask unnecessary questions
- ask before disclosing when social ordering matters
- reveal information in manageable units
- maintain conversational coherence across turns
- stay useful, not theatrical
- preserve the feeling of active presence during tool latency
- make pauses feel intentional, not broken

The system should feel thoughtful, not performatively human.

---

## 18. Success Metrics

### Primary Metrics

- reduction in “answer dump” rate
- increase in multi-turn completion satisfaction
- increase in user-rated naturalness
- increase in user-rated appropriateness of pacing

### Additional Success Metrics

- **Turn-Taking Parity:** Reduction in average word count per agent response, indicating information is being delivered in smaller, human-like chunks.
- **Interruption Success Rate:** Percentage of context switches successfully handled without losing original state data.
- **Perceived Latency:** Time to first token for filler messages must be under 500 ms to preserve the illusion of active presence.

### Secondary Metrics

- lower abandonment after first answer
- higher follow-up engagement
- lower rate of “too verbose” complaints
- lower rate of “robotic / unnatural” feedback

### Internal Evaluation Metrics

- percent of turns where disclosure policy matched policy rubric
- percent of mixed-valence scenarios where preference question was asked
- percent of turns where WM correctly tracked undisclosed items
- percent of turns where response stayed within one intended act
- percent of yields successfully resumed with intact state

---

## 19. Evaluation Plan

### Offline Eval Set

Create scenario sets for:

- mixed good/bad news
- simple factual Q&A
- emotionally sensitive information
- multi-option tasks
- ambiguous user requests
- users who prefer concise style
- users who prefer interactive style
- interrupted conversations that resume later

### Human Rubric

Rate each response on:

- naturalness
- pacing
- appropriateness
- conversational coherence
- non-robotic delivery
- usefulness
- quality of turn-taking

### A/B Testing

Compare:

- base agent
- post-processing-only layer
- full dialogue-policy + WM layer

Hypothesis:
The full layer should outperform post-processing alone in perceived naturalness, pacing, and interruption handling.

---

## 20. Risks and Mitigations

### Risk: Too many extra questions

The system may become annoying.

**Mitigation:**

- add direct-answer default for simple tasks
- track user tolerance for back-and-forth

### Risk: Latency increase

Policy pass plus critique pass may add delay.

**Mitigation:**

- keep policy rules lightweight
- make critique optional or cheap
- stream filler quickly when tool use is required

### Risk: WM becomes noisy

Too much transient state can degrade performance.

**Mitigation:**

- use structured schema
- add expiry and decay rules
- keep only actionable short-lived items

### Risk: Mismatch with core agent output

Core agent may still return final prose instead of structured facts.

**Mitigation:**

- define an adapter layer
- request normalized outputs when possible
- extract facts into WM before response generation

### Risk: System feels manipulative

Too much staged disclosure may feel artificial.

**Mitigation:**

- use gradual delivery only when justified
- keep utility above style

### Risk: Interrupted state corruption

Suspended dialogue threads may get lost or merged incorrectly.

**Mitigation:**

- persist explicit resumable state
- separate active and suspended tasks
- add state integrity checks on resume

---

## 21. Technical Requirements

### Integration

- must wrap existing agent API
- must support pluggable core agents
- must read transcript context
- must write and read WM state per session

### Storage

- session transcript store
- episodic summary store
- short-lived WM store
- optional persistent user preference store
- resumable graph execution state

### Interfaces

Suggested core agent interface:

- `get_facts(query, context)`
- `get_options(query, context)`
- `perform_action(action_spec)`
- `summarize_result(result)`

Suggested conversation layer interface:

- `decide_next_act(state, user_message)`
- `update_working_memory(state, event)`
- `select_disclosure_item(state)`
- `realize_response(act, state)`
- `yield_or_continue(state)`
- `resume_from_saved_state(session_id)`

---

## 22. Roadmap

### Phase 1: MVP

- build WM schema
- build rule-based policy engine
- support 6 to 10 dialogue acts
- integrate with one core agent
- implement yield/resume state handling
- add basic filler streaming
- run offline evals

### Phase 2: Smarter Adaptation

- learn user delivery preferences
- improve disclosure prioritization
- better critique/revision logic
- better interruption recovery

### Phase 3: Advanced Orchestration

- support multiple core agents
- dynamic policy learning
- deeper session planning
- richer social/contextual handling

---

## 23. Open Questions

- How much WM should be model-visible versus system-managed?
- Should the layer operate before every core-agent call or only on selected turns?
- What is the best structured contract for core-agent outputs?
- How should transient user signals be promoted into long-term preferences?
- How do we prevent gradual delivery from reducing efficiency for power users?
- How should multiple suspended dialogue threads be prioritized on resume?

---

## 24. Recommended First Implementation

Build the first version as a **pre-and-post orchestration layer**, not just a response chunker.

It should:

- intercept the user message
- choose a dialogue act
- call the core agent only for missing knowledge
- store results as undisclosed facts in WM
- reveal one step at a time
- update plan after each user turn
- support yield/resume in a graph runtime
- emit quick filler when tool latency would otherwise feel dead

That gives the best balance of feasibility, modularity, and conversational quality.

---

## 25. One-Line Product Definition

**A reusable dialogue policy and working-memory layer that makes any agent speak more like a thoughtful human by deciding not just what it knows, but what it should say next.**
