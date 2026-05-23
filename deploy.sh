#!/usr/bin/env bash
#
# Hermes MCP Bridge — Deployment Script
# ======================================
# Sets up a virtualenv, installs dependencies, and creates a systemd service.
#
# Usage:
#   sudo bash deploy.sh
#
# Or to customize the install directory:
#   MCP_BRIDGE_DIR=/srv/mcp-bridge sudo bash deploy.sh
#
# Prerequisites:
#   - Debian/Ubuntu (or any systemd-based Linux)
#   - Python 3.10+ with venv support
#   - (Optional) WireGuard already configured for secure remote access
#
set -euo pipefail

APP_DIR="${MCP_BRIDGE_DIR:-/opt/hermes-mcp-bridge}"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_NAME="hermes-mcp-bridge"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
USER="${SUDO_USER:-root}"
GROUP="${USER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Hermes MCP Bridge Deploy ==="
echo ""
echo "  Install dir: ${APP_DIR}"
echo "  Service:     ${SERVICE_NAME}"
echo "  User:        ${USER}"
echo ""

# ── 1. Create app directory ─────────────────────────────────────────────────
echo "[1/6] Creating ${APP_DIR}..."
mkdir -p "${APP_DIR}"

# Only copy files if the script is not running from the install directory itself
if [ "${SCRIPT_DIR}" != "${APP_DIR}" ]; then
    cp "${SCRIPT_DIR}/server.py" "${APP_DIR}/"
    echo "      Copied server.py to ${APP_DIR}/"
else
    echo "      Running from install dir, skipping copy (files already in place)"
fi

chown -R "${USER}:${GROUP}" "${APP_DIR}"

# ── 2. Check for .env ───────────────────────────────────────────────────────
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "[2/6] No .env file found at ${APP_DIR}/.env"
    if [ -f "${SCRIPT_DIR}/.env.example" ]; then
        cp "${SCRIPT_DIR}/.env.example" "${APP_DIR}/.env"
        echo "      Copied .env.example to ${APP_DIR}/.env"
        echo "      >>> EDIT THIS FILE before starting the service! <<<"
        echo ""
        echo "      Required: set MCP_BRIDGE_TOKEN to a strong random value"
        echo "      Optional: set MCP_BRIDGE_HOST to this machine's IP"
    else
        echo "      Create one based on .env.example with your MCP_BRIDGE_TOKEN"
    fi
else
    echo "[2/6] .env already exists at ${APP_DIR}/.env"
fi

# ── 3. Create virtualenv ────────────────────────────────────────────────────
echo "[3/6] Creating virtualenv..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip -q
if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    "${VENV_DIR}/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt" -q
else
    "${VENV_DIR}/bin/pip" install fastapi uvicorn -q
fi

# ── 4. Verify installation ──────────────────────────────────────────────────
echo "[4/6] Verifying installation..."
"${VENV_DIR}/bin/python" -c "import fastapi; import uvicorn; print('  OK: fastapi', fastapi.__version__)"

# ── 5. Create systemd service ───────────────────────────────────────────────
echo "[5/6] Creating systemd service: ${SERVICE_NAME}..."
cat > "${SERVICE_FILE}" << SYSTEMD
[Unit]
Description=Hermes MCP Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hermes-mcp-bridge

# Hardening (remove if causes issues on your system)
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/tmp

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload

# ── 6. Done ─────────────────────────────────────────────────────────────────
echo "[6/6] Done!"
echo ""
echo "Next steps:"
echo "  1. Edit ${APP_DIR}/.env and set MCP_BRIDGE_TOKEN"
echo "     Generate: openssl rand -hex 32"
echo "  2. Set MCP_BRIDGE_HOST to this machine's IP (e.g. WireGuard IP)"
echo "  3. Start:  sudo systemctl start ${SERVICE_NAME}"
echo "  4. Enable: sudo systemctl enable ${SERVICE_NAME}"
echo "  5. Check:  sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "Test with:"
echo "  curl -H 'Authorization: Bearer YOUR_TOKEN' http://HOST:8000/health"
