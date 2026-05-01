#!/usr/bin/env bash
set -euo pipefail

SERVICE_PREFIX="chanakya"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this script with sudo:"
  echo "  sudo ./scripts/uninstall-autostart-ubuntu.sh"
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required but not found."
  exit 1
fi

systemctl disable --now "${SERVICE_PREFIX}.target" >/dev/null 2>&1 || true
systemctl disable --now "${SERVICE_PREFIX}-app.service" >/dev/null 2>&1 || true
systemctl disable --now "${SERVICE_PREFIX}-conversation-layer.service" >/dev/null 2>&1 || true
systemctl disable --now "${SERVICE_PREFIX}-air.service" >/dev/null 2>&1 || true

rm -f "$SYSTEMD_DIR/${SERVICE_PREFIX}.target"
rm -f "$SYSTEMD_DIR/${SERVICE_PREFIX}-app.service"
rm -f "$SYSTEMD_DIR/${SERVICE_PREFIX}-conversation-layer.service"
rm -f "$SYSTEMD_DIR/${SERVICE_PREFIX}-air.service"

systemctl daemon-reload

echo "Removed autostart services for ${SERVICE_PREFIX}."
