#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIR_DIR="$ROOT_DIR/AI-Router-AIR"
RUNTIME_DIR="$ROOT_DIR/build/runtime"
mkdir -p "$RUNTIME_DIR"

AIR_PID_FILE="$RUNTIME_DIR/air_server.pid"
CHANAKYA_PID_FILE="$RUNTIME_DIR/chanakya.pid"
AIR_LOG_FILE="$RUNTIME_DIR/air_server.log"
CHANAKYA_LOG_FILE="$RUNTIME_DIR/chanakya.log"

PYTHON_BIN="${PYTHON_BIN:-python}"
AIR_PORT="${AIR_SERVER_PORT:-5012}"
CHANAKYA_PORT="${CHANAKYA_PORT:-5000}"

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

start_process \
  "AIR server" \
  "$AIR_PID_FILE" \
  "$AIR_LOG_FILE" \
  bash -lc "cd '$AIR_DIR' && exec env PYTHONUNBUFFERED=1 SERVER_PORT='$AIR_PORT' '$PYTHON_BIN' -m server.main"

start_process \
  "Chanakya" \
  "$CHANAKYA_PID_FILE" \
  "$CHANAKYA_LOG_FILE" \
  bash -lc "cd '$ROOT_DIR' && exec env PYTHONUNBUFFERED=1 AIR_SERVER_URL='http://localhost:$AIR_PORT' '$PYTHON_BIN' -m flask --app chanakya.app run --host 0.0.0.0 --port '$CHANAKYA_PORT'"

printf '\nAIR dashboard: http://localhost:%s\n' "$AIR_PORT"
printf 'Chanakya app:  http://localhost:%s\n' "$CHANAKYA_PORT"
printf 'Use scripts/stop_chanakya_air.sh to stop both services.\n'
