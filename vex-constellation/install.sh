#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="${HOME}/.hermes/plugins/vex-constellation"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== VEX Constellation v1.0.0 — Install ==="
echo ""

mkdir -p "${PLUGIN_DIR}"
cp "${SCRIPT_DIR}/plugin.yaml" "${PLUGIN_DIR}/"
cp "${SCRIPT_DIR}/__init__.py" "${PLUGIN_DIR}/"
cp "${SCRIPT_DIR}/run_constellation.py" "${PLUGIN_DIR}/"
cp "${SCRIPT_DIR}/vex_autoresponder.py" "${PLUGIN_DIR}/"
cp "${SCRIPT_DIR}/vex-autoresponder.service" "${PLUGIN_DIR}/"
echo "✓ Plugin files copied to ${PLUGIN_DIR}"

if command -v hermes &>/dev/null; then
    hermes plugins enable vex-constellation 2>/dev/null || \
        echo "  Add 'vex-constellation' to plugins.enabled in ~/.hermes/config.yaml"
    echo "✓ Plugin enabled"
else
    echo "⚠ hermes CLI not found. Add 'vex-constellation' to plugins.enabled manually."
fi

echo ""
echo "=== Done ==="
echo ""
echo "Quick start:"
echo "  /constellation start"
echo "  /constellation announce http://<peer>:839"
echo "  /constellation peers"
echo ""
echo "Port 839. V-E-X. Zero governance."
