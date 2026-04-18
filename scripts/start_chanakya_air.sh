#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIR_DIR="$ROOT_DIR/AI-Router-AIR"
CONVERSATION_LAYER_DIR="$ROOT_DIR/chanakya_conversation_layer"
A2A_APP_DIR="$CONVERSATION_LAYER_DIR/a2a_example_app"
RUNTIME_DIR="$ROOT_DIR/build/runtime"
mkdir -p "$RUNTIME_DIR"

AIR_PID_FILE="$RUNTIME_DIR/air_server.pid"
CHANAKYA_PID_FILE="$RUNTIME_DIR/chanakya.pid"
CONVERSATION_LAYER_PID_FILE="$RUNTIME_DIR/chanakya_conversation_layer.pid"
A2A_OPENCODE_PID_FILE="$RUNTIME_DIR/a2a_opencode.pid"
A2A_BRIDGE_PID_FILE="$RUNTIME_DIR/a2a_bridge.pid"
AIR_LOG_FILE="$RUNTIME_DIR/air_server.log"
CHANAKYA_LOG_FILE="$RUNTIME_DIR/chanakya.log"
CONVERSATION_LAYER_LOG_FILE="$RUNTIME_DIR/chanakya_conversation_layer.log"
A2A_OPENCODE_LOG_FILE="$RUNTIME_DIR/a2a_opencode.log"
A2A_BRIDGE_LOG_FILE="$RUNTIME_DIR/a2a_bridge.log"

PYTHON_BIN="${PYTHON_BIN:-python}"
AIR_PORT="${AIR_SERVER_PORT:-5512}"
CHANAKYA_PORT="${CHANAKYA_PORT:-5513}"
CONVERSATION_LAYER_HOST="${CONVERSATION_LAYER_HOST:-127.0.0.1}"
CONVERSATION_LAYER_PORT="${CONVERSATION_LAYER_PORT:-5514}"
OPENCODE_HOST="${OPENCODE_HOST:-127.0.0.1}"
OPENCODE_PORT="${OPENCODE_PORT:-18496}"
A2A_HOST="${A2A_HOST:-127.0.0.1}"
A2A_PORT="${A2A_PORT:-18770}"
MODE="${1:-core}"
ROOT_ENV_FILE="${ENV_FILE_PATH:-$ROOT_DIR/.env}"

if [[ -f "$ROOT_ENV_FILE" ]]; then
  set -a
  source "$ROOT_ENV_FILE"
  set +a
fi

export ENV_FILE_PATH="$ROOT_ENV_FILE"

start_process() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid="$(<"$pid_file")"
    if kill -0 "$existing_pid" 2>/dev/null; then
      printf '%s is already running (pid %s)\n' "$name" "$existing_pid"
      return
    fi
    rm -f "$pid_file"
  fi

  nohup "$@" >>"$log_file" 2>&1 &
  local pid=$!
  printf '%s' "$pid" >"$pid_file"
  printf 'Started %s (pid %s). Log: %s\n' "$name" "$pid" "$log_file"
}

start_a2a_stack() {
  start_process \
    "OpenCode server" \
    "$A2A_OPENCODE_PID_FILE" \
    "$A2A_OPENCODE_LOG_FILE" \
    bash -lc "cd '$A2A_APP_DIR' && exec env PYTHONUNBUFFERED=1 OPENCODE_HOST='$OPENCODE_HOST' OPENCODE_PORT='$OPENCODE_PORT' bash '$A2A_APP_DIR/start_opencode.sh'"

  start_process \
    "A2A bridge" \
    "$A2A_BRIDGE_PID_FILE" \
    "$A2A_BRIDGE_LOG_FILE" \
    bash -lc "cd '$A2A_APP_DIR' && exec env PYTHONUNBUFFERED=1 OPENCODE_HOST='$OPENCODE_HOST' OPENCODE_PORT='$OPENCODE_PORT' A2A_HOST='$A2A_HOST' A2A_PORT='$A2A_PORT' bash '$A2A_APP_DIR/start_bridge.sh'"
}

start_process \
  "AIR server" \
  "$AIR_PID_FILE" \
  "$AIR_LOG_FILE" \
  bash -lc "cd '$AIR_DIR' && exec env PYTHONUNBUFFERED=1 SERVER_PORT='$AIR_PORT' '$PYTHON_BIN' -m server.main"

start_process \
  "Chanakya conversation layer" \
  "$CONVERSATION_LAYER_PID_FILE" \
  "$CONVERSATION_LAYER_LOG_FILE" \
  bash -lc "cd '$CONVERSATION_LAYER_DIR' && exec env PYTHONUNBUFFERED=1 FLASK_APP=app APP_HOST='$CONVERSATION_LAYER_HOST' APP_PORT='$CONVERSATION_LAYER_PORT' OPENAI_BASE_URL='http://localhost:$AIR_PORT/v1' CONVERSATION_OPENAI_BASE_URL='http://localhost:$AIR_PORT/v1' '$PYTHON_BIN' -m flask run --host '$CONVERSATION_LAYER_HOST' --port '$CONVERSATION_LAYER_PORT'"

if [[ "$MODE" == "a2a" || "$MODE" == "core+a2a" ]]; then
  start_a2a_stack
fi

start_process \
  "Chanakya" \
  "$CHANAKYA_PID_FILE" \
  "$CHANAKYA_LOG_FILE" \
  bash -lc "cd '$ROOT_DIR' && exec env PYTHONUNBUFFERED=1 AIR_SERVER_URL='http://localhost:$AIR_PORT' A2A_AGENT_URL='http://$A2A_HOST:$A2A_PORT' '$PYTHON_BIN' -m flask --app chanakya.app run --host 0.0.0.0 --port '$CHANAKYA_PORT'"

printf '\nAIR dashboard: http://localhost:%s\n' "$AIR_PORT"
printf 'Conversation layer: http://%s:%s\n' "$CONVERSATION_LAYER_HOST" "$CONVERSATION_LAYER_PORT"
if [[ "$MODE" == "a2a" || "$MODE" == "core+a2a" ]]; then
  printf 'OpenCode server:     http://%s:%s\n' "$OPENCODE_HOST" "$OPENCODE_PORT"
  printf 'A2A bridge:          http://%s:%s\n' "$A2A_HOST" "$A2A_PORT"
fi
printf 'Chanakya app:  http://localhost:%s\n' "$CHANAKYA_PORT"
printf 'Usage: scripts/start_chanakya_air.sh [core|a2a|core+a2a]\n'
printf 'Use scripts/stop_chanakya_air.sh to stop all services.\n'
