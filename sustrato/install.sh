#!/usr/bin/env bash
# =============================================================================
# Sustrato — Install script for Hermes Agent
# Installs the sustrato plugin into ~/.hermes/plugins/sustrato/
# =============================================================================
set -euo pipefail

PLUGIN_DIR="${HOME}/.hermes/plugins/sustrato"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Sustrato v1.0.0 — Install ==="
echo ""

# Copy plugin files
mkdir -p "${PLUGIN_DIR}"
cp "${SCRIPT_DIR}/plugin.yaml" "${PLUGIN_DIR}/"
cp "${SCRIPT_DIR}/__init__.py" "${PLUGIN_DIR}/"
echo "✓ Plugin files copied to ${PLUGIN_DIR}"

# Install CLI tool
CLI_DEST="${HOME}/.local/bin/sustrato"
mkdir -p "$(dirname "${CLI_DEST}")"
cp "${SCRIPT_DIR}/sustrato" "${CLI_DEST}"
chmod +x "${CLI_DEST}"
echo "✓ CLI tool installed to ${CLI_DEST}"

# Enable in Hermes config if not already enabled
if command -v hermes &>/dev/null; then
    if hermes plugins list 2>/dev/null | grep -q "sustrato"; then
        echo "✓ Plugin 'sustrato' found in Hermes"
    else
        echo "→ Enabling plugin..."
        hermes plugins enable sustrato 2>/dev/null || \
            echo "  (add 'sustrato' to plugins.enabled in ~/.hermes/config.yaml)"
    fi
else
    echo "⚠ hermes CLI not found. Add 'sustrato' to plugins.enabled in ~/.hermes/config.yaml"
fi

# Create default config
if [ ! -f "${HOME}/.hermes/sustrato.yaml" ]; then
    cat > "${HOME}/.hermes/sustrato.yaml" << 'EOF'
# Sustrato — Multi-layer provider failover
# Chain: primary → secondary → tertiary
chain: []
auto_failover: true
health_check_timeout: 5
EOF
    echo "✓ Default config created at ~/.hermes/sustrato.yaml"
else
    echo "✓ Config already exists at ~/.hermes/sustrato.yaml"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Quick start:"
echo "  hermes sustrato add deepseek deepseek-v4-pro"
echo "  hermes sustrato add openrouter anthropic/claude-sonnet-4"
echo "  hermes sustrato add ollama llama3:70b --local"
echo "  hermes sustrato list"
echo ""
echo "Restart Hermes or /reset to activate."
