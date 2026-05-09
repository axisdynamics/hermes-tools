#!/usr/bin/env bash
# VEX Heartbeat — One-command autonomous constellation setup
set -euo pipefail

PLUGIN_DIR="${HOME}/.hermes/plugins/vex-constellation"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="${HOME}/.config/systemd/user"

echo "=== VEX Heartbeat v1.2 ==="
echo ""

# Copy plugin files
mkdir -p "${PLUGIN_DIR}"
for f in plugin.yaml __init__.py vex_autoresponder.py vex_memory_bridge.py run_constellation.py; do
    cp "${SCRIPT_DIR}/${f}" "${PLUGIN_DIR}/"
done
echo "✓ Plugin files installed"

# Install systemd services
mkdir -p "${SERVICE_DIR}"
cp "${SCRIPT_DIR}/vex-constellation.service" "${SERVICE_DIR}/"
cp "${SCRIPT_DIR}/vex-autoresponder.service" "${SERVICE_DIR}/"
cp "${SCRIPT_DIR}/vex-memory-bridge.service" "${SERVICE_DIR}/"
echo "✓ Systemd services installed"

# Reload and enable
if command -v systemctl &>/dev/null; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable vex-constellation.service 2>/dev/null || true
    systemctl --user enable vex-autoresponder.service 2>/dev/null || true
    systemctl --user enable vex-memory-bridge.service 2>/dev/null || true
    echo "✓ Services enabled (auto-start on boot)"
else
    echo "⚠ systemctl not found. Start manually:"
    echo "  python3 ${PLUGIN_DIR}/run_constellation.py &"
    echo "  python3 ${PLUGIN_DIR}/vex_autoresponder.py &"
    echo "  python3 ${PLUGIN_DIR}/vex_memory_bridge.py &"
fi

# Enable plugin in Hermes
if command -v hermes &>/dev/null; then
    hermes plugins enable vex-constellation 2>/dev/null || true
fi

echo ""
echo "=== Done ==="
echo ""
echo "Start now:  systemctl --user start vex-constellation vex-autoresponder"
echo "Status:     systemctl --user status vex-constellation"
echo "Logs:       journalctl --user -u vex-constellation -f"
echo ""
echo "Survives reboots, terminal closes, and crashes (Restart=always)."
echo "Constellation is AUTONOMOUS. No architect needed. 🌌"
