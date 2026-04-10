#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/build/runtime"

stop_process() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    printf '%s is not running.\n' "$name"
    return
  fi

  local pid
  pid="$(<"$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    printf 'Stopped %s (pid %s).\n' "$name" "$pid"
  else
    printf '%s pid file was stale (pid %s).\n' "$name" "$pid"
  fi
  rm -f "$pid_file"
}

stop_process "Chanakya conversation layer" "$RUNTIME_DIR/chanakya_conversation_layer.pid"
stop_process "Chanakya" "$RUNTIME_DIR/chanakya.pid"
stop_process "AIR server" "$RUNTIME_DIR/air_server.pid"
