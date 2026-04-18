#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

REDIS_CONTAINER_NAME="${REDIS_CONTAINER_NAME:-chanakya-redis}"

stop_process() {
  local name="$1"
  local pid_file="$2"

  if [[ ! -f "$pid_file" ]]; then
    printf '%s is not running\n' "$name"
    return
  fi

  local pid
  pid="$(<"$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    printf 'Stopped %s (pid %s)\n' "$name" "$pid"
  else
    printf '%s pid file was stale\n' "$name"
  fi
  rm -f "$pid_file"
}

stop_redis_container() {
  if ! command -v docker >/dev/null 2>&1; then
    return
  fi

  local existing_state
  existing_state="$(docker inspect -f '{{.State.Status}}' "$REDIS_CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$existing_state" == "running" ]]; then
    docker stop "$REDIS_CONTAINER_NAME" >/dev/null
    printf 'Stopped redis container %s\n' "$REDIS_CONTAINER_NAME"
    return
  fi
  if [[ -n "$existing_state" ]]; then
    printf 'redis container %s is not running\n' "$REDIS_CONTAINER_NAME"
    return
  fi
  printf 'redis container %s does not exist\n' "$REDIS_CONTAINER_NAME"
}

stop_process "chanakya app" "$ROOT_DIR/.chanakya-app.pid"
stop_process "a2a bridge" "$ROOT_DIR/.chanakya-bridge.pid"
stop_process "opencode server" "$ROOT_DIR/.chanakya-opencode.pid"
stop_redis_container
