#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

AIR_PORT="${AIR_SERVER_PORT:-5512}"
CHANAKYA_PORT="${CHANAKYA_PORT:-5513}"

export ENV_FILE_PATH="$ROOT_ENV_FILE"
export PYTHONUNBUFFERED=1
export AIR_SERVER_URL="http://localhost:${AIR_PORT}"

cd "$ROOT_DIR"
exec "$PYTHON_BIN" -m flask --app chanakya.app run --host 0.0.0.0 --port "$CHANAKYA_PORT"
