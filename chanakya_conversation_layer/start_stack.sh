#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A2A_DIR="$ROOT_DIR/a2a_example_app"
MODE="${1:-app}"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-5000}"
REDIS_CONTAINER_NAME="${REDIS_CONTAINER_NAME:-chanakya-redis}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
REDIS_HOST_PORT="${REDIS_HOST_PORT:-6387}"
REDIS_CONTAINER_PORT="${REDIS_CONTAINER_PORT:-6379}"

APP_LOG="$ROOT_DIR/.chanakya-app.log"
APP_PID_FILE="$ROOT_DIR/.chanakya-app.pid"
OPENCODE_LOG="$ROOT_DIR/.chanakya-opencode.log"
OPENCODE_PID_FILE="$ROOT_DIR/.chanakya-opencode.pid"
BRIDGE_LOG="$ROOT_DIR/.chanakya-bridge.log"
BRIDGE_PID_FILE="$ROOT_DIR/.chanakya-bridge.pid"

start_process() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  if [[ -f "$pid_file" ]]; then
    local existing_pid
    existing_pid="$(<"$pid_file")"
    if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
      printf '%s already running with pid %s\n' "$name" "$existing_pid"
      return
    fi
    rm -f "$pid_file"
  fi

  nohup "$@" >"$log_file" 2>&1 &
  local pid=$!
  printf '%s' "$pid" >"$pid_file"
  printf 'Started %s (pid %s)\n' "$name" "$pid"
}

start_redis_container() {
  if [[ "${CONVERSATION_STATE_STORE_BACKEND:-memory}" != "redis" ]]; then
    return
  fi

  if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is required to start the Redis container\n' >&2
    exit 1
  fi

  local existing_state
  existing_state="$(docker inspect -f '{{.State.Status}}' "$REDIS_CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$existing_state" == "running" ]]; then
    printf 'redis container already running on port %s\n' "$REDIS_HOST_PORT"
    return
  fi
  if [[ -n "$existing_state" ]]; then
    docker start "$REDIS_CONTAINER_NAME" >/dev/null
    printf 'Started redis container %s on port %s\n' "$REDIS_CONTAINER_NAME" "$REDIS_HOST_PORT"
    return
  fi

  docker run -d \
    --name "$REDIS_CONTAINER_NAME" \
    -p "$REDIS_HOST_PORT:$REDIS_CONTAINER_PORT" \
    "$REDIS_IMAGE" >/dev/null
  printf 'Started redis container %s on port %s\n' "$REDIS_CONTAINER_NAME" "$REDIS_HOST_PORT"
}

start_app() {
  start_redis_container
  start_process \
    "chanakya app" \
    "$APP_PID_FILE" \
    "$APP_LOG" \
    bash -lc "cd \"$ROOT_DIR\" && exec env FLASK_APP=app flask run --host \"$APP_HOST\" --port \"$APP_PORT\""
}

start_a2a_stack() {
  if [[ ! -d "$A2A_DIR" ]]; then
    printf 'a2a_example_app directory not found\n' >&2
    exit 1
  fi

  start_process \
    "opencode server" \
    "$OPENCODE_PID_FILE" \
    "$OPENCODE_LOG" \
    bash "$A2A_DIR/start_opencode.sh"

  start_process \
    "a2a bridge" \
    "$BRIDGE_PID_FILE" \
    "$BRIDGE_LOG" \
    bash "$A2A_DIR/start_bridge.sh"
}

case "$MODE" in
  app)
    start_app
    ;;
  app+a2a)
    start_redis_container
    start_a2a_stack
    start_app
    ;;
  a2a)
    start_a2a_stack
    ;;
  *)
    printf 'Usage: %s [app|a2a|app+a2a]\n' "$(basename "$0")" >&2
    exit 1
    ;;
esac

printf '\nLogs:\n'
printf '  app: %s\n' "$APP_LOG"
printf '  opencode: %s\n' "$OPENCODE_LOG"
printf '  bridge: %s\n' "$BRIDGE_LOG"
