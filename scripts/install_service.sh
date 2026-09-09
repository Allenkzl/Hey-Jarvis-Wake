#!/bin/sh
# Install hey-jarvis-wake as a systemd service (Linux).
#
# Usage:
#   sudo scripts/install_service.sh [INSTALL_DIR] [SERVICE_USER] [EXTRA_ENV]
#
# Examples:
#   # default: install from the repository's own path, run as the
#   # invoking (root) user's target — better pass the pollen-style user:
#   sudo scripts/install_service.sh /opt/hey-jarvis-wake voiceuser
#
#   # pass extra Environment= lines (semicolon separated):
#   sudo scripts/install_service.sh /opt/hey-jarvis-wake pollen \
#     "WAKE_INPUT_DEVICE=reachymini_audio_src WAKE_OUTPUT_DEVICE=reachymini_audio_sink"
set -eu

INSTALL_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE_USER="${2:-${SUDO_USER:-$(id -un)}}"
EXTRA_ENV="${3:-}"

UNIT=/etc/systemd/system/hey-jarvis-wake.service
TEMPLATE="$(dirname "$0")/../deploy/hey-jarvis-wake.service"

# Ensure the runtime user can read/execute the install dir.
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null || true

{
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g; s|__SERVICE_USER__|$SERVICE_USER|g" "$TEMPLATE"
    if [ -n "$EXTRA_ENV" ]; then
        for kv in $EXTRA_ENV; do
            echo "Environment=$kv"
        done
    fi
} > "$UNIT"

systemctl daemon-reload
systemctl enable --now hey-jarvis-wake.service
echo "Installed: $UNIT"
systemctl status hey-jarvis-wake.service --no-pager | head -8
