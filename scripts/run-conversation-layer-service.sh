#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONVERSATION_LAYER_DIR="$ROOT_DIR/apps/chanakya_conversation_layer"
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

AIR_PORT="${AIR_SERVER_PORT:-5512}"
APP_HOST="${CONVERSATION_LAYER_HOST:-127.0.0.1}"
APP_PORT="${CONVERSATION_LAYER_PORT:-5514}"

export ENV_FILE_PATH="$ROOT_ENV_FILE"
export PYTHONUNBUFFERED=1
export FLASK_APP=app
export APP_HOST
export APP_PORT
export OPENAI_BASE_URL="http://localhost:${AIR_PORT}/v1"
export CONVERSATION_OPENAI_BASE_URL="http://localhost:${AIR_PORT}/v1"

cd "$CONVERSATION_LAYER_DIR"
exec "$PYTHON_BIN" -m flask run --host "$APP_HOST" --port "$APP_PORT"
