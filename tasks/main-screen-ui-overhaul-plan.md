# Chanakya Main Screen UI Overhaul Plan

## Scope

This plan applies only to the main Chanakya screen rendered by `chanakya/templates/index.html`.

Out of scope for this pass:
- `chanakya/templates/work.html`
- AIR backend behavior and endpoints
- conversational layer behavior and routing
- existing main-screen backend API contracts

## Goal

Redesign the main Chanakya screen so it feels much closer to the reference UI in `@/home/diogenes/projects/samosa/my-forks/Chanakya-Mishra`, while preserving the current Chanakya main-screen capabilities.

This should be a reference-inspired port, not a direct frontend code transplant.

## Product Decisions

The following decisions are fixed for the first implementation pass:

1. The main screen should start in orb-first mode.
2. Task Progress should remain persistently visible on desktop for now.
3. The visual style can differ slightly from the reference, but should remain broadly similar.

## Constraints

- Keep `work.html` unchanged.
- Keep existing backend contracts unchanged, especially:
  - `/api/chat`
  - `/api/runtime-config`
  - `/api/a2a/options`
  - `/api/stream`
- Keep AIR voice behavior driven by the current `chanakya/static/js/air_voice.js` flow.
- Do not remove existing main-screen power-user features without relocating them.
- Prefer preserving current DOM IDs and script hooks where practical to minimize JS rewiring risk.

## Current Main-Screen Responsibilities

The current main screen in `chanakya/templates/index.html` includes:

- primary chat surface
- queued conversation status
- task progress panel
- settings panel
- voice panel
- agents panel
- notifications panel
- debug log popout and debug trace content
- SSE-driven live updates

The redesign must preserve these behaviors while changing the surface presentation.

## Target UX

The new main screen should have:

- a centered assistant shell rather than a full-width dashboard layout
- a strong visual identity closer to the reference UI
- a central orb or hero state area
- compact top-bar controls
- an orb-first initial state
- an expandable chat area
- a reference-style bottom action dock for text and voice actions
- a persistent desktop-visible task progress region
- advanced controls still available on desktop, but visually compressed

## Recommended Layout

### Primary Shell

Rebuild `index.html` around a single main assistant shell:

- top bar
- orb stage
- expandable chat region
- bottom composer and voice dock

This shell should be the clear visual focus.

### Desktop Utility Region

Keep a secondary desktop-visible utility region, but make it lighter and less dominant than the current right sidebar.

Recommended visible-on-desktop sections:
- Task Progress
- Voice
- Settings

Recommended collapsed or secondary sections:
- Agents
- Notifications

Recommended detached or popout sections:
- Request Trace
- Tool Traces
- Task Timeline
- Sandbox Guide
- Temporary Subagents

## Feature Mapping

### Keep Existing JS/Data Contracts

Preserve current IDs and hooks where possible, especially:

- `chatLog`
- `chatForm`
- `messageInput`
- `conversationQueueStatus`
- `taskProgress`
- `runtimeConfigApplyButton`
- `airMicButton`
- `airVoiceLoopButton`
- `airSpeakButton`
- `requestTrace`
- `toolTraceList`
- `taskList`
- `taskTimeline`
- `subagentList`
- `agentList`
- notification form IDs

This keeps the current inline script and event wiring mostly intact.

### Orb and State Mapping

Add a central orb stage to the main screen and map it to current system states:

- idle
- listening
- speaking
- queued or busy
- error

The orb must reflect current app state rather than introducing a new interaction model.

### Composer and Action Dock

Replace the plain composer with a reference-style dock that still uses the current controls.

Expected controls:
- message input
- send button
- mic button
- voice mode toggle
- speak reply button
- optional pause replies button

These should continue to call the current Chanakya handlers.

### Chat Reveal Behavior

The screen should start in orb-first mode.

That means:
- orb stage is the default visual focus on first load
- chat panel is initially minimized or hidden
- user can reveal the chat area through an explicit toggle
- once expanded, chat should behave normally without changing underlying message logic

## Implementation Phases

### Phase 1: Restructure `index.html`

Rebuild the main template structure around:

- centered assistant shell
- orb stage
- expandable chat section
- compact desktop utility region

During this phase:
- preserve current IDs where possible
- do not rewrite backend-facing logic
- do not touch `work.html`

### Phase 2: Port the Reference-Inspired Visual System

Introduce the new main-screen visual language:

- rounded shell
- neon-dark styling
- stronger hierarchy and spacing
- compact icon-led controls
- orb visuals

The result should feel close to the reference but still compatible with Chanakya's current product identity.

### Phase 3: Reposition Current Main-Screen Controls

Keep these visible on desktop:
- Task Progress
- Voice
- Settings

Move these into lower-emphasis surfaces:
- Agents
- Notifications

Keep debug-heavy content out of the primary visual area.

### Phase 4: Wire Orb and UI State Feedback

Connect the new orb and status strip to the existing main-screen runtime state:

- AIR voice activity
- queue state
- errors
- assistant reply playback state

Do not replace `air_voice.js`; adapt the UI around it.

### Phase 5: Extract and Organize Styling

Move the new main-screen styling into shared static CSS if practical.

Preferred outcome:
- large inline CSS in `index.html` is reduced substantially
- page styling becomes easier to maintain

If needed, keep JS inline for the first pass to reduce migration risk.

### Phase 6: Regression Verification

Manually verify the main screen still supports:

- text chat send and receive
- queued response updates
- task progress rendering
- runtime config loading and apply
- A2A option refresh
- AIR model loading
- mic, voice loop, speak reply, pause replies
- debug log popout
- agents UI reachability
- notification UI reachability

Then run:

```bash
python -m ruff check chanakya/
python -m mypy chanakya/
pytest chanakya/test
```

## Risks

### Main Risk

The main risk is not styling. It is preserving the current high-functionality screen while making it feel visually close to a much simpler reference UI.

### Specific Risk Areas

- breaking existing DOM-based event wiring in `index.html`
- making advanced controls too hidden for desktop usage
- creating layout regressions in the debug and settings surfaces
- introducing state mismatches between orb visuals and actual AIR/chat state

## Recommended Delivery Strategy

Use the fastest safe path first:

1. preserve the current frontend logic
2. replace markup and CSS carefully
3. preserve IDs and major hooks
4. validate behavior before deeper cleanup

After the visual shell is stable, do a second cleanup pass if needed to improve maintainability.

## Definition of Done

The first pass is complete when:

- the main Chanakya screen visually resembles the reference direction
- it starts in orb-first mode
- task progress remains visible on desktop
- advanced desktop controls remain reachable
- `work.html` is unchanged
- existing main-screen APIs and voice behavior still work
- manual checks pass and the standard verification commands complete successfully
