# Chanakya Chat Latency Optimization Plan

## Current Architecture (Sequential, Blocking)

```
User speaks → STT → POST /api/chat
                         │
                         ▼
                   [Core Agent: MAFRuntime.run()]
                         │  full LLM call (sync, blocking)
                         ▼
                   [Conversation Layer: orchestration LLM]
                         │  2nd LLM call to rewrite/chunk text (sync, blocking)
                         ▼
                   ┌──── chat_service returns ChatReply JSON ────┐
                   │                                            │
User ← audio playback ← TTS POST /v1/audio/speech ← Frontend processes
                         (stream:false — full blob wait)
```

**Three sequential blocking latencies:**
1. Core agent LLM call (slowest — seconds per response)
2. Conversation layer orchestration LLM (500ms–3s extra per message)
3. TTS audio synthesis — full MP3 blob before playback starts

---

## Phase 1 — Streaming TTS on the Frontend (frontend-only, 1–2 days)

**Problem:** `air_voice.js:644` uses `stream: false`, downloads the entire MP3 blob before playing.

**Solution:** Set `stream: true` and use `MediaSource` API to play audio incrementally:

```javascript
// air_voice.js — synthesizeSpeechChunk
const response = await fetch(`${baseUrl}/v1/audio/speech`, {
  method: "POST",
  body: JSON.stringify({ model, input: text, voice, response_format: "mp3", stream: true }),
});
// Pipe response.body (ReadableStream) into a MediaSource SourceBuffer
```

**Changes:** Only `apps/chanakya/static/js/air_voice.js`.
**Payoff:** First audio plays 2–5× faster. No backend changes needed. The AIR proxy engine already supports `stream: true` with raw byte streaming.

---

## Phase 2 — SSE Streaming for the Core Agent (backend + frontend, 3–5 days)

**Problem:** `chat_service.py` is entirely synchronous — the frontend waits for the full pipeline before getting any data.

**Solution:** Add a `GET /api/chat/stream` SSE endpoint that:

1. Starts the core agent run in a background thread via `asyncio.to_thread` or a dedicated executor
2. Streams tokens as `data: {"type":"token","text":"..."}` SSE events as soon as MAFRuntime yields them
3. When the core agent finishes, runs the conversation layer in the background
4. Streams conversation layer results (timed message chunks) as they're ready
5. Frontend triggers TTS at sentence boundaries while later tokens are still being generated

### Backend changes:

| File | Change |
|------|--------|
| `chat_service.py` | New `chat_stream()` method that yields tokens via an `asyncio.Queue` |
| `agent/runtime.py` | Expose a streaming variant of `run()` that yields tokens via callback/queue |
| `app.py` | New `GET /api/chat/stream` route with SSE `StreamingResponse` |

### Frontend changes:

| File | Change |
|------|--------|
| `index.html` | Subscribe to SSE, buffer tokens, trigger TTS at sentence boundaries |
| `air_voice.js` | New `synthesizeStreaming()` function using `MediaSource` |

**Payoff:** User hears the first sentence while the rest is still being generated. TTS runs concurrently with text generation.



## Summary

| Phase | What | Where | Effort | Latency Reduction |
|-------|------|-------|--------|-------------------|
| 1 | Streaming TTS (`stream:true` + MediaSource) | `air_voice.js` | 1–2 days | ~40% (faster first audio) |
| 2 | SSE streaming for core agent | `chat_service.py` + frontend | 3–5 days | ~60% (overlap gen + playback) |

Both phases preserve the conversation layer's features (TTS optimization, chunked delivery, queued follow-ups, interruption handling). Each phase is independent and deployable separately.
