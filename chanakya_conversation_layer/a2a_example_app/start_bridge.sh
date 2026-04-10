#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OPENCODE_HOST="${OPENCODE_HOST:-127.0.0.1}"
OPENCODE_PORT="${OPENCODE_PORT:-18496}"
A2A_HOST="${A2A_HOST:-127.0.0.1}"
A2A_PORT="${A2A_PORT:-18770}"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi
export OPENCODE_BASE_URL="${OPENCODE_BASE_URL:-http://${OPENCODE_HOST}:${OPENCODE_PORT}}"
export A2A_PUBLIC_URL="${A2A_PUBLIC_URL:-http://${A2A_HOST}:${A2A_PORT}}"

exec python "$ROOT_DIR/opencode_a2a_bridge.py" --host "$A2A_HOST" --port "$A2A_PORT"
