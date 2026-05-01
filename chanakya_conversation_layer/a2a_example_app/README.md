# ChatFlash

ChatFlash is a small FastAPI web app for chatting with OpenCode agents.

It uses Microsoft Agent Framework for the chat layer. By default, the active MAF agent is an `A2AAgent` that talks to the OpenCode A2A bridge. The code also includes a commented `Agent + OpenAIChatClient` block so you can switch to a local OpenAI-compatible backend later without changing the rest of the app.

The A2A path now persists the remote `context_id`, so multi-turn memory works across follow-up messages in the same chat session.

## Files

- `chatflash/maf_chat.py` - the single MAF chat service
- `chatflash/app.py` - FastAPI app
- `chatflash/store.py` - SQLite session/message storage
- `opencode_a2a_bridge.py` - OpenCode A2A bridge
- `run_webapp.py` - web app entrypoint

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Terminal 1:

```bash
./start_opencode.sh
```

Terminal 2:

```bash
./start_bridge.sh
```

Terminal 3:

```bash
./start_webapp.sh
```

Open:

```text
http://127.0.0.1:18550
```

## How it works

- the web app stores chat sessions in `chatflash.db`
- `chatflash/maf_chat.py` creates the active MAF agent
- by default, that active agent is `A2AAgent(url=...)`
- the OpenCode agent dropdown selects remote OpenCode agents like `build` and `plan`
- the A2A bridge routes the chosen OpenCode agent using a small message prefix
- the app stores the returned A2A `context_id` and sends it back on later turns so OpenCode keeps conversation memory

## Switching from A2A to local OpenAI-compatible

Edit `chatflash/maf_chat.py`.

Right now the active block is:

```python
return ActiveAgent(
    backend="a2a",
    instance=A2AAgent(
        name="OpenCode A2A",
        description="Microsoft Agent Framework A2A agent for OpenCode.",
        url=self.settings.opencode_a2a_url,
    ),
)
```

To switch to local OpenAI-compatible chat:

1. comment that block
2. uncomment the `Agent(... OpenAIChatClient(...))` block directly below it
3. restart `./start_webapp.sh`

The rest of the app stays the same.

## Config

### Web app

- `CHATFLASH_HOST` - default `127.0.0.1`
- `CHATFLASH_PORT` - default `18550`
- `CHATFLASH_DB_PATH` - default `chatflash.db`

### OpenCode / A2A

- `CHATFLASH_OPENCODE_HTTP_URL` - default `http://127.0.0.1:18496`
- `CHATFLASH_OPENCODE_A2A_URL` - default `http://127.0.0.1:18770`
- `CHATFLASH_DEFAULT_REMOTE_AGENTS` - fallback list, default `build,plan`
- `CHATFLASH_SESSION_HISTORY_LIMIT` - transcript excerpt size used only for fallback recovery, default `18`

### Local OpenAI-compatible fallback

- `CHATFLASH_MODEL_BASE_URL` - default `http://192.168.1.51:1234/v1`
- `CHATFLASH_MODEL_API_KEY` - default `na`
- `CHATFLASH_MODEL_ID` - default `qwen/qwen3.6-35b-a3b`

## Troubleshooting

- if OpenCode is busy, check `ss -ltnp '( sport = :18496 )'`
- if the A2A bridge is busy, check `ss -ltnp '( sport = :18770 )'`
- if the web app port is busy, check `ss -ltnp '( sport = :18550 )'`
- stop an old listener with `fuser -k 18496/tcp`, `fuser -k 18770/tcp`, or `fuser -k 18550/tcp`
- if OpenCode seems to forget earlier turns, make sure you are continuing inside the same ChatFlash session instead of creating a new one
- if A2A memory is interrupted, the app falls back to a transcript excerpt, but the best memory comes from a healthy reused `context_id`

## Verified

- web app routes load
- ChatFlash works through MAF `A2AAgent` to OpenCode via the bridge
- the OpenCode agent prefix routing still works for agent selection
- multi-turn memory works in A2A mode by reusing the stored remote `context_id`
