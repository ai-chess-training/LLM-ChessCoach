#!/bin/bash
set -e

# Ubuntu VPS Setup Script for LLM Chess Coach
# Run this script on a fresh Ubuntu 20.04+ VPS

APP_DIR="/opt/llm-chess-coach"
APP_USER="chesscoach"
DOMAIN=""  # Set your domain or leave empty

echo "=========================================="
echo "LLM Chess Coach - Ubuntu VPS Setup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Update system
echo "[1/12] Updating system packages..."
apt-get update
apt-get upgrade -y

# Install required packages
echo "[2/12] Installing required packages..."
apt-get install -y \
    python3.9 \
    python3.9-venv \
    python3-pip \
    nginx \
    git \
    stockfish \
    fail2ban \
    ufw \
    certbot \
    python3-certbot-nginx \
    logrotate \
    htop \
    curl \
    wget

# Create application user
echo "[3/12] Creating application user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/bash -d $APP_DIR -m $APP_USER
    echo "Created user: $APP_USER"
else
    echo "User $APP_USER already exists"
fi

# Create application directory structure
echo "[4/12] Creating directory structure..."
mkdir -p $APP_DIR/{logs,games,samples,scripts}
chown -R $APP_USER:$APP_USER $APP_DIR

# Clone repository (you'll need to do this manually or set up deploy keys)
echo "[5/12] Cloning application repository..."
if [ ! -d "$APP_DIR/.git" ]; then
    echo "Please clone your repository to $APP_DIR"
    echo "Example: cd $APP_DIR && sudo -u $APP_USER git clone <your-repo-url> ."
else
    echo "Repository already cloned"
fi

# Create Python virtual environment
echo "[6/12] Creating Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    sudo -u $APP_USER python3.9 -m venv $APP_DIR/venv
    sudo -u $APP_USER $APP_DIR/venv/bin/pip install --upgrade pip setuptools wheel
fi

# Install Python dependencies (if requirements.txt exists)
echo "[7/12] Installing Python dependencies..."
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u $APP_USER $APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt
fi

# Configure UFW firewall
echo "[8/12] Configuring firewall..."
ufw --force enable
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw status

# Install systemd service
echo "[9/12] Installing systemd service..."
if [ -f "$APP_DIR/systemd/llm-chess-coach.service" ]; then
    cp $APP_DIR/systemd/llm-chess-coach.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable llm-chess-coach.service
    echo "Service installed but not started (configure .env first)"
else
    echo "Warning: systemd service file not found at $APP_DIR/systemd/llm-chess-coach.service"
fi

# Configure nginx
echo "[10/12] Configuring nginx..."
if [ -f "$APP_DIR/nginx/llm-chess-coach.conf" ]; then
    # Create backup of default config
    if [ -f "/etc/nginx/sites-enabled/default" ]; then
        mv /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.bak
    fi

    # Copy and update nginx config
    cp $APP_DIR/nginx/llm-chess-coach.conf /etc/nginx/sites-available/llm-chess-coach

    if [ ! -z "$DOMAIN" ]; then
        sed -i "s/your-domain.com/$DOMAIN/g" /etc/nginx/sites-available/llm-chess-coach
    fi

    ln -sf /etc/nginx/sites-available/llm-chess-coach /etc/nginx/sites-enabled/
    nginx -t && systemctl restart nginx
    echo "Nginx configured"
else
    echo "Warning: nginx config file not found at $APP_DIR/nginx/llm-chess-coach.conf"
fi

# Create environment file template
echo "[11/12] Creating .env template..."
cat > $APP_DIR/.env.template << 'EOF'
# Production Environment Configuration

# Required: API Authentication
API_KEY=your-secure-api-key-here

# Required: OpenAI API
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4

# Optional: AI API (if using alternative provider)
AI_API_KEY=
AI_API_ENDPOINT=
AI_MODEL_NAME=

# Stockfish Configuration
STOCKFISH_PATH=/usr/games/stockfish
MULTIPV=5
NODES_PER_PV=1000000

# Security
ALLOWED_ORIGINS=https://your-domain.com,https://www.your-domain.com

# Application
LOG_LEVEL=info
GUNICORN_WORKERS=4

# Optional: Lichess Integration
LICHESS_API_TOKEN=
EOF

chown $APP_USER:$APP_USER $APP_DIR/.env.template

echo "[12/12] Setup complete!"
echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo "1. Configure environment variables:"
echo "   cp $APP_DIR/.env.template $APP_DIR/.env"
echo "   nano $APP_DIR/.env"
echo ""
echo "2. Set up SSL certificate (if you have a domain):"
echo "   Run: sudo bash $APP_DIR/scripts/setup_ssl.sh your-domain.com"
echo ""
echo "3. Start the application:"
echo "   systemctl start llm-chess-coach.service"
echo "   systemctl status llm-chess-coach.service"
echo ""
echo "4. View logs:"
echo "   journalctl -u llm-chess-coach.service -f"
echo ""
echo "5. Run server hardening script:"
echo "   sudo bash $APP_DIR/scripts/harden_server.sh"
echo "=========================================="
