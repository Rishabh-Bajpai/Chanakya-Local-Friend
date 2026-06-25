#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIR_DIR="$ROOT_DIR/apps/AI-Router-AIR"
ROOT_ENV_FILE="${ENV_FILE_PATH:-$ROOT_DIR/.env}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  printf 'No Python binary found (checked PYTHON_BIN, .venv, and PATH)\n' >&2
  exit 1
fi

if [[ -f "$ROOT_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_ENV_FILE"
  set +a
fi

export ENV_FILE_PATH="$ROOT_ENV_FILE"
export PYTHONUNBUFFERED=1
export SERVER_PORT="${AIR_SERVER_PORT:-5512}"

cd "$AIR_DIR"
exec "$PYTHON_BIN" -m uvicorn server.main:app --host 0.0.0.0 --port "$SERVER_PORT"
