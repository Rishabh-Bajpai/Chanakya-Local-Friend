# Chrome TTS Playback — Known Issues

## Status

Chanakya's TTS (text-to-speech) audio playback works correctly on
**Firefox 152.0.1** (verified on the host machine). On Chromium-based
browsers (Chrome, Edge, Brave, Opera), the TTS pipeline fails to
produce audio in some scenarios. Multiple remediation attempts were
made and did not fully resolve the issue. The Chrome-specific changes
were reverted. This document records what was tried so future
investigation can pick up where the work left off.

## Verified working

| Browser              | Version  | Mic button | External `/api/voice-command` | Notes                          |
| -------------------- | -------- | ---------- | ------------------------------ | ------------------------------ |
| Firefox              | 152.0.1  | Yes        | Yes                            | All multi-segment TTS works    |
| Chromium / Chrome    | n/a      | Broken     | Broken                         | See symptoms and attempts below |

## Symptoms on Chromium

1. **Mic button**: response text is rendered in the chat, but no
   audio plays.
2. **External `POST /api/voice-command`** (curl / external app):
   - Only the first response segment is rendered
   - No audio plays for any segment
3. **SSE-driven playback** (the browser is the receiving end of the
   `/api/voice-command` flow): no audio plays; TTS segments may be
   partially rendered depending on backend data shape.
4. **No console errors** are visible by default. Failures are
   silent in the browser console at the `console.debug` / `console.warn`
   level.

## What was tried

The Chrome TTS fix work spanned two commits before being reverted:

- `4d54510` — "Fix Chrome TTS playback: AudioContext silent-kick +
  Web Speech fallback"
- `003383d` — "Force Web Speech on Chrome + add diagnostic logging"

### Attempt 1: `unlockAudioOnFirstGesture` and `ensurePlaybackAudioCtx`

- Idea: prime the AudioContext on the user's first click so the
  browser's autoplay gesture applies.
- Implementation: a one-time `document` click handler creates the
  AudioContext and dispatches a custom `air-audio-unlocked` event;
  the SSE handler calls `ensurePlaybackAudioCtx` before playing TTS.
- Result: did not fix the bug. On Chrome the context still went
  `suspended` after the click handler returned.

### Attempt 2: `playSilentKick` — silent buffer to keep context `running`

- Idea: Chrome aggressively suspends AudioContexts that aren't
  actively producing sound. Starting a 1-frame silent buffer forces
  the context into the `running` state and keeps it there.
- Implementation: a helper function that creates a 1-sample silent
  AudioBuffer, connects it to the destination, and calls `start(0)`.
  Called from `initPlaybackAudioCtx` and from `tryAudioCtxPlayback`
  when the context is `suspended`.
- Result: did not fix the bug. The 1-sample buffer ends in
  ~1/48000 seconds, after which Chrome re-suspends the context.
  A longer-running kick might help but was not implemented.

### Attempt 3: `speakWithWebSpeech` fallback

- Idea: use the Web Speech API (`window.speechSynthesis.speak(new
  SpeechSynthesisUtterance(text))`) as a guaranteed-to-work fallback
  when the AudioContext path fails.
- Implementation: a `speakWithWebSpeech(text)` helper that wraps
  the call in a Promise. Called from `speakText` when
  `isAudioCtxHealthy()` reports a non-running context, and as a
  post-fetch fallback if the context went `suspended` during the
  AIR fetch.
- Result: partially working. In some scenarios the Web Speech
  fallback fired and produced audio; in others, the
  `speakAssistantMessageAndWait` call inside the SSE for-loop
  silently threw and the loop bailed out before the fallback was
  reached.

### Attempt 4: Force Web Speech on Chrome via user-agent detection

- Idea: detect Chrome / Edge / Opera via `navigator.userAgent` and
  skip the AIR TTS path entirely on those browsers, routing all
  `speakText` calls through the Web Speech API.
- Implementation: `isChromeLike` flag at module init. `speakText`
  short-circuits on Chrome with `if (isChromeLike) { await
  speakWithWebSpeech(cleaned); return; }`.
- Result: did not fully fix the bug. The user reported the
  single-segment-only symptom persisted.

### Attempt 5: Diagnostic logging

- Idea: add comprehensive `console.log` and `console.warn` calls
  in `speakText` and the SSE `voice_reply` handler so the actual
  Chrome failure point is visible in DevTools.
- Result: this revealed an important secondary insight — see the
  "Secondary observation" section below.

## Secondary observation

The diagnostic logging surfaced that **the backend only sent one
TTS segment for the user's test input ("tell me two jokes")**. The
SSE event's `data.messages` array had `length: 1`, not 3. This
means:

- The "only one message displayed" symptom may not be purely a
  Chrome bug. It can also be a backend data-shape issue: the
  conversation layer paces the LLM's response into TTS segments
  with delays, but for some inputs it returns the response as a
  single segment.
- Multi-segment TTS only works when the LLM produces a long
  response that the conversation layer decides to pace. For short
  responses, the array has length 1 and the multi-segment code
  path is never exercised.

This means some of the "Chrome is broken" reports may have been
about single-segment responses where the bug was elsewhere (or
where the bug was the silent audio with no console errors, but
not the multi-segment issue).

## Root cause hypothesis (best guess)

The most likely root cause is **Chrome's strict AudioContext
autoplay handling combined with the absence of a user gesture in
the SSE-driven code path**:

1. SSE event fires (no gesture in the call stack)
2. `speakAssistantMessageAndWait` -> `speakText` runs
3. AudioContext was created earlier in a click scope, so it
   exists, but Chrome has since suspended it
4. The `await ctx.resume()` is a no-op (no gesture in scope)
5. `decodeAudioData` succeeds, but `source.start(0)` queues audio
   that never plays
6. The 2-second timeout fires, `tryAudioCtxPlayback` returns
   `"timeout"`
7. Fallback to `new Audio().play()` is also blocked by Chrome's
   autoplay policy (no gesture in scope)
8. The error is caught silently (was `console.debug`; later
   changed to `console.warn` to surface in DevTools)

The user had previously reported that even the simple mic-button
path is broken on Chrome (not just the SSE path), which is harder
to explain under this hypothesis because the mic button is
triggered by a click. The most likely explanation is that the
context is being suspended between the click and the TTS playback
call due to the time it takes to fetch the LLM response + the AIR
TTS audio.

## What to try next (for future investigation)

1. **Longer-lived silent kick**: replace the 1-frame buffer with a
   1-2 second looped silent buffer that keeps the context "active"
   for a long enough window to cover the LLM fetch roundtrip.
2. **Probe with a longer LLM response**: test with a prompt that
   reliably produces a multi-segment conversation-layer response,
   to confirm whether the single-segment symptom is data-shape
   only or also affects multi-segment.
3. **Test with Web Speech directly**: try the
   `speakWithWebSpeech(text)` helper in isolation in the Chrome
   console after a user gesture, to confirm whether the Web Speech
   API is reliably producing audio on Chrome.
4. **Switch TTS `response_format` to WAV**: Chrome's
   `new Audio(url)` may handle `audio/wav` more reliably than
   `audio/mpeg` in the autoplay-blocked state.
5. **Investigate `MediaSession` API**: hook `playbackAudioCtx`
   into a `MediaSession` so Chrome shows media controls and
   unlocks media-style autoplay.
6. **Bypass the AudioContext entirely on Chrome**: on Chrome,
   send the TTS audio as a base64 data URL directly to a
   `new Audio("data:audio/mpeg;base64,...")` element, which is
   sometimes treated differently from blob URLs in Chrome's
   autoplay handling.
7. **Check the network layer**: confirm there is no CORS or
   cookie issue on the AIR TTS request from Chrome.
8. **Check Chrome extensions**: ad blockers and privacy
   extensions can interfere with audio playback on Chrome.

## Revert reference

- Revert commit: see git log — the most recent commit on the
  relevant branch reverts both `4d54510` and `003383d`.
- The `isChromeLike` user-agent detection, the Web Speech helpers
  (`speakWithWebSpeech`, `isAudioCtxHealthy`), the `playSilentKick`
  helper, the diagnostic `console.log` calls, and the
  `try/catch` block in the SSE `voice_reply` handler are all
  gone from `air_voice.js` and `index.html`.
- The Firefox behavior should be identical to the pre-investigation
  baseline.
