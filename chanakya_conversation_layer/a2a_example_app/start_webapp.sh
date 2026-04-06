#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOST="${CHATFLASH_HOST:-127.0.0.1}"
PORT="${CHATFLASH_PORT:-18550}"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi
exec python -m uvicorn run_webapp:app --app-dir "$ROOT_DIR" --host "$HOST" --port "$PORT"
