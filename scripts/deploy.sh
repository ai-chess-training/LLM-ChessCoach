#!/bin/bash
set -e

# LLM Chess Coach Deployment Script
# This script deploys the application to the production server

APP_DIR="/opt/llm-chess-coach"
APP_USER="chesscoach"
VENV_DIR="$APP_DIR/venv"
REPO_URL="https://github.com/your-username/LLM-ChessCoach.git"  # Update with your repo

echo "=========================================="
echo "LLM Chess Coach Deployment"
echo "=========================================="

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo"
    exit 1
fi

# Navigate to application directory
cd "$APP_DIR"

echo "[1/7] Pulling latest changes from git..."
sudo -u $APP_USER git pull origin master

echo "[2/7] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "[3/7] Installing/updating dependencies..."
sudo -u $APP_USER "$VENV_DIR/bin/pip" install --upgrade pip
sudo -u $APP_USER "$VENV_DIR/bin/pip" install -r requirements.txt

echo "[4/7] Running database migrations (if any)..."
# Add migration commands here if you use a database

echo "[5/7] Collecting static files (if any)..."
# Add static file collection here if needed

echo "[6/7] Restarting application service..."
systemctl restart llm-chess-coach.service

echo "[7/7] Checking service status..."
sleep 3
if systemctl is-active --quiet llm-chess-coach.service; then
    echo "✓ Service is running"
    systemctl status llm-chess-coach.service --no-pager -l
else
    echo "✗ Service failed to start"
    echo "Checking logs:"
    journalctl -u llm-chess-coach.service -n 50 --no-pager
    exit 1
fi

echo ""
echo "=========================================="
echo "Deployment completed successfully!"
echo "=========================================="
echo "Application logs: journalctl -u llm-chess-coach.service -f"
echo "Application status: systemctl status llm-chess-coach.service"
