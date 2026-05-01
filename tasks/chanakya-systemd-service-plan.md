# Chanakya Systemd Service Plan

## Scope

- Implement Ubuntu/Linux `systemd` support for the Chanakya `core` stack only.
- Do not support `core+a2a` in the first version.
- Keep `scripts/start_chanakya_air.sh` and `scripts/stop_chanakya_air.sh` for local development.

## Environment Strategy

- The installer must strictly require a repo-root virtual environment at `.venv`.
- Do not support `conda` activation in `systemd` units.
- Do not support `--python-bin` overrides.
- The expected setup is a single Python 3.11 virtual environment with all three Python projects installed:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]
pip install -e ./AI-Router-AIR
pip install -e ./chanakya_conversation_layer
```

## Target Service Layout

- `chanakya.target`
- `chanakya-air.service`
- `chanakya-conversation-layer.service`
- `chanakya-app.service`

## Why Separate Units

- The current startup flow in `scripts/start_chanakya_air.sh` is designed for development and uses `nohup`, PID files, and shared log files under `build/runtime/`.
- `systemd` should supervise the real long-running processes directly.
- Separate units provide cleaner restart behavior, better status visibility, and simpler troubleshooting through `journalctl`.

## Required Runtime Behavior To Preserve

The `systemd` implementation must preserve the current runtime contract established by `scripts/start_chanakya_air.sh`:

- Source repo-root `.env` when present
- Export `ENV_FILE_PATH`
- Default ports:
  - `AIR_SERVER_PORT=5512`
  - `CHANAKYA_PORT=5513`
  - `CONVERSATION_LAYER_PORT=5514`
- Ensure the Chanakya app sees:
  - `AIR_SERVER_URL=http://localhost:$AIR_SERVER_PORT`
- Ensure the conversation layer sees:
  - `OPENAI_BASE_URL=http://localhost:$AIR_SERVER_PORT/v1`
  - `CONVERSATION_OPENAI_BASE_URL=http://localhost:$AIR_SERVER_PORT/v1`

This is required because:

- `chanakya/config.py` relies on environment variables already being available.
- `chanakya_conversation_layer/core_agent_app/config.py` uses `ENV_FILE_PATH` and environment-driven URLs.

## Runner Scripts

Add small service-specific runner scripts that prepare environment and then `exec` the final process:

- `scripts/run-air-service.sh`
- `scripts/run-conversation-layer-service.sh`
- `scripts/run-chanakya-service.sh`

These scripts should:

- Resolve the repo root
- Verify `.venv/bin/python` exists
- Source repo-root `.env` if present
- Export `ENV_FILE_PATH`
- Set the required service-specific env values
- `exec` the final long-running process

## AIR Service Launch Strategy

- Do not launch AIR via `python -m server.main` under `systemd`.
- `AI-Router-AIR/server/main.py` enables `reload=True` when run as `__main__`, which is inappropriate for a supervised service.
- Launch AIR with `uvicorn server.main:app --host 0.0.0.0 --port ...` instead.

## Installer Script

Add `scripts/install-autostart-ubuntu.sh` modeled on the reference repo's installer, adapted to Chanakya.

Responsibilities:

- Require Linux with `systemd`
- Require `sudo`
- Resolve target user from `--user` or `SUDO_USER`
- Verify the target user exists
- Verify repo-root `.venv/bin/python` exists and is executable
- Write unit files to `/etc/systemd/system`
- Run `systemctl daemon-reload`
- Enable and restart `chanakya.target`
- Print useful follow-up commands

Supported installer arguments:

- `--user <name>`

## Uninstall Script

Add `scripts/uninstall-autostart-ubuntu.sh`.

Responsibilities:

- Require `sudo`
- Stop and disable `chanakya.target`
- Stop and disable the three component services
- Remove unit files from `/etc/systemd/system`
- Run `systemctl daemon-reload`

## Unit Dependencies

### `chanakya-air.service`

- `After=network-online.target`
- `Wants=network-online.target`
- `PartOf=chanakya.target`

### `chanakya-conversation-layer.service`

- `After=chanakya-air.service`
- `Requires=chanakya-air.service`
- `PartOf=chanakya.target`

### `chanakya-app.service`

- `After=chanakya-air.service chanakya-conversation-layer.service`
- `Requires=chanakya-air.service chanakya-conversation-layer.service`
- `PartOf=chanakya.target`

### Common service settings

- `Type=simple`
- `User=<target-user>`
- `WorkingDirectory=<repo-root>`
- `Restart=always`
- `RestartSec=2`

## Logging Strategy

- Use `journald` for `systemd`-managed services.
- Do not reuse `build/runtime/*.log` or PID files for `systemd` processes.
- Leave `build/runtime/` behavior unchanged for the existing manual startup scripts.

## Documentation Changes

Update:

- `README.md`
- `chanakya/README.md`

Document:

- `.venv` setup steps
- service installation command
- status, restart, and `journalctl` commands
- uninstall command
- local development still uses `scripts/start_chanakya_air.sh core`

## Validation Checklist

### Static checks

- Review generated unit file contents
- Run `bash -n` on all new shell scripts
- Run `systemd-analyze verify` on generated units when available

### Runtime checks

- Install the stack in `core` mode
- Confirm:
  - AIR dashboard on `http://localhost:5512`
  - conversation layer on `http://127.0.0.1:5514`
  - Chanakya app on `http://localhost:5513`
- Restart `chanakya.target` and confirm services recover correctly
- Confirm services start automatically after reboot

## Files Expected To Be Added Or Updated

New files:

- `tasks/chanakya-systemd-service-plan.md`
- `scripts/run-air-service.sh`
- `scripts/run-conversation-layer-service.sh`
- `scripts/run-chanakya-service.sh`
- `scripts/install-autostart-ubuntu.sh`
- `scripts/uninstall-autostart-ubuntu.sh`

Updated files:

- `README.md`
- `chanakya/README.md`

## Implementation Notes

- Keep changes minimal and isolated from the existing development startup flow.
- The first implementation is intentionally limited to `core` mode.
- If A2A support is added later, it should extend this design with additional units instead of changing the `core` service model.
