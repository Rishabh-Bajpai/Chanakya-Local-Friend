#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIR_DIR="$ROOT_DIR/AI-Router-AIR"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
ROOT_ENV_FILE="${ENV_FILE_PATH:-$ROOT_DIR/.env}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf 'Python virtual environment is missing at %s\n' "$PYTHON_BIN" >&2
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
