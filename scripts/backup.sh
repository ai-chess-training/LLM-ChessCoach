#!/bin/bash
set -e

# LLM Chess Coach Backup Script
# Creates backups of environment config and user data

APP_DIR="/opt/llm-chess-coach"
BACKUP_DIR="/var/backups/llm-chess-coach"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="llm-chess-coach_${DATE}.tar.gz"

echo "=========================================="
echo "LLM Chess Coach Backup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "[1/5] Creating temporary backup directory..."
TEMP_DIR=$(mktemp -d)
mkdir -p "$TEMP_DIR/llm-chess-coach"

echo "[2/5] Backing up environment configuration..."
if [ -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env" "$TEMP_DIR/llm-chess-coach/"
    echo "✓ .env backed up"
fi

echo "[3/5] Backing up user data and schedules..."
if [ -f "$APP_DIR/schedules.json" ]; then
    cp "$APP_DIR/schedules.json" "$TEMP_DIR/llm-chess-coach/"
    echo "✓ schedules.json backed up"
fi

if [ -d "$APP_DIR/games" ]; then
    cp -r "$APP_DIR/games" "$TEMP_DIR/llm-chess-coach/"
    echo "✓ games/ backed up"
fi

echo "[4/5] Creating compressed archive..."
cd "$TEMP_DIR"
tar -czf "$BACKUP_DIR/$BACKUP_NAME" llm-chess-coach/
cd - > /dev/null

echo "[5/5] Cleaning up..."
rm -rf "$TEMP_DIR"

# Keep only last 30 days of backups
find "$BACKUP_DIR" -name "llm-chess-coach_*.tar.gz" -mtime +30 -delete

echo ""
echo "=========================================="
echo "Backup completed successfully!"
echo "=========================================="
echo "Backup location: $BACKUP_DIR/$BACKUP_NAME"
echo "Backup size: $(du -h "$BACKUP_DIR/$BACKUP_NAME" | cut -f1)"
echo ""
echo "To restore:"
echo "  1. Extract: tar -xzf $BACKUP_DIR/$BACKUP_NAME"
echo "  2. Copy files to $APP_DIR"
echo "  3. Restart service: systemctl restart llm-chess-coach.service"
echo "=========================================="
