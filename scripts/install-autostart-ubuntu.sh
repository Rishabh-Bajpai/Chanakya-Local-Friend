#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_PREFIX="chanakya"
APP_USER="${SUDO_USER:-$USER}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Ubuntu/Linux with systemd."
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required but not found."
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this script with sudo:"
  echo "  sudo ./scripts/install-autostart-ubuntu.sh"
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      if [[ -z "${2:-}" ]]; then
        echo "Missing username. Example: sudo ./scripts/install-autostart-ubuntu.sh --user <username>"
        exit 1
      fi
      APP_USER="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Supported options: --user <name>"
      exit 1
      ;;
  esac
done

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "User '$APP_USER' does not exist on this machine."
  exit 1
fi

APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [[ -z "$APP_HOME" || ! -d "$APP_HOME" ]]; then
  echo "Unable to determine home directory for user '$APP_USER'."
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python virtual environment not found at: $ROOT_DIR/.venv"
  echo "Create it and install all required packages before installing the service:"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install --upgrade pip"
  echo "  pip install -e .[dev]"
  echo "  pip install -e ./AI-Router-AIR"
  echo "  pip install -e ./chanakya_conversation_layer"
  exit 1
fi

COMMON_PATH="$ROOT_DIR/.venv/bin:$APP_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

cat >"$SYSTEMD_DIR/${SERVICE_PREFIX}-air.service" <<EOF
[Unit]
Description=Chanakya AIR server
After=network-online.target
Wants=network-online.target
PartOf=${SERVICE_PREFIX}.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$ROOT_DIR
Environment=PATH=$COMMON_PATH
Environment=ENV_FILE_PATH=$ROOT_DIR/.env
ExecStart=$ROOT_DIR/scripts/run-air-service.sh
Restart=always
RestartSec=2

[Install]
WantedBy=${SERVICE_PREFIX}.target
EOF

cat >"$SYSTEMD_DIR/${SERVICE_PREFIX}-conversation-layer.service" <<EOF
[Unit]
Description=Chanakya conversation layer
After=${SERVICE_PREFIX}-air.service
Requires=${SERVICE_PREFIX}-air.service
PartOf=${SERVICE_PREFIX}.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$ROOT_DIR
Environment=PATH=$COMMON_PATH
Environment=ENV_FILE_PATH=$ROOT_DIR/.env
ExecStart=$ROOT_DIR/scripts/run-conversation-layer-service.sh
Restart=always
RestartSec=2

[Install]
WantedBy=${SERVICE_PREFIX}.target
EOF

cat >"$SYSTEMD_DIR/${SERVICE_PREFIX}-app.service" <<EOF
[Unit]
Description=Chanakya Flask app
After=${SERVICE_PREFIX}-air.service ${SERVICE_PREFIX}-conversation-layer.service
Requires=${SERVICE_PREFIX}-air.service ${SERVICE_PREFIX}-conversation-layer.service
PartOf=${SERVICE_PREFIX}.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$ROOT_DIR
Environment=PATH=$COMMON_PATH
Environment=ENV_FILE_PATH=$ROOT_DIR/.env
ExecStart=$ROOT_DIR/scripts/run-chanakya-service.sh
Restart=always
RestartSec=2

[Install]
WantedBy=${SERVICE_PREFIX}.target
EOF

cat >"$SYSTEMD_DIR/${SERVICE_PREFIX}.target" <<EOF
[Unit]
Description=Chanakya core stack
Wants=${SERVICE_PREFIX}-air.service ${SERVICE_PREFIX}-conversation-layer.service ${SERVICE_PREFIX}-app.service
After=network-online.target

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_PREFIX}.target"
systemctl restart "${SERVICE_PREFIX}.target"

echo "Installed and started systemd services."
echo "- Target: ${SERVICE_PREFIX}.target"
echo "- AIR dashboard:       http://127.0.0.1:5512"
echo "- Conversation layer:  http://127.0.0.1:5514"
echo "- Chanakya app:        http://127.0.0.1:5513"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_PREFIX}.target"
echo "  sudo journalctl -u ${SERVICE_PREFIX}-air.service -f"
echo "  sudo journalctl -u ${SERVICE_PREFIX}-conversation-layer.service -f"
echo "  sudo journalctl -u ${SERVICE_PREFIX}-app.service -f"
