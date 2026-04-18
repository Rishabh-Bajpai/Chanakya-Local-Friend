#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

A2A_HOST="${A2A_HOST:-127.0.0.1}"
A2A_PORT="${A2A_PORT:-18770}"
PROMPT="${1:-Say hello in one short sentence}"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi
exec python "$ROOT_DIR/a2a_client_demo.py" --server-url "http://${A2A_HOST}:${A2A_PORT}" "$PROMPT"
