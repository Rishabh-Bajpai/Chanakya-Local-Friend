#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT="${OPENCODE_PORT:-18496}"
HOST="${OPENCODE_HOST:-127.0.0.1}"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi
exec opencode serve --hostname "$HOST" --port "$PORT"
