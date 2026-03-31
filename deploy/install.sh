#!/bin/bash
# install.sh — Deploy AI Trading Team as a systemd service on Ubuntu
set -e

APP_DIR="/opt/trading-team"
SERVICE_USER="trader"
SERVICE_FILE="/etc/systemd/system/trading-team.service"

echo "============================================"
echo "  AI Trading Team — Ubuntu Service Installer"
echo "============================================"
echo

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root (sudo ./install.sh)"
    exit 1
fi

# Create service user if needed
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "[1/6] Creating service user '$SERVICE_USER'..."
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
else
    echo "[1/6] Service user '$SERVICE_USER' already exists."
fi

# Install Python dependencies
echo "[2/6] Installing Python dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv
pip3 install --quiet -r "$APP_DIR/requirements.txt"

# Copy application
echo "[3/6] Deploying application to $APP_DIR..."
mkdir -p "$APP_DIR"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='*.db' --exclude='.env' \
    "$(dirname "$(dirname "$(readlink -f "$0")")")/" "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# Check .env
if [ ! -f "$APP_DIR/signals/.env" ]; then
    echo
    echo "  WARNING: No .env file found at $APP_DIR/signals/.env"
    echo "  Copy the example and add your Alpaca API keys:"
    echo "    sudo cp $APP_DIR/signals/.env.example $APP_DIR/signals/.env"
    echo "    sudo nano $APP_DIR/signals/.env"
    echo
fi

# Install systemd service
echo "[4/6] Installing systemd service..."
cp "$APP_DIR/deploy/trading-team.service" "$SERVICE_FILE"
systemctl daemon-reload

# Enable service
echo "[5/6] Enabling service..."
systemctl enable trading-team.service

echo "[6/6] Done."
echo
echo "============================================"
echo "  Service installed. Commands:"
echo "    sudo systemctl start trading-team"
echo "    sudo systemctl stop trading-team"
echo "    sudo systemctl status trading-team"
echo "    sudo journalctl -u trading-team -f"
echo
echo "  Dashboard: http://localhost:5050"
echo "  Default login: admin / admin123"
echo "============================================"
