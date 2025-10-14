#!/bin/bash
set -e

# SSL Certificate Setup with Let's Encrypt
# Usage: sudo bash setup_ssl.sh your-domain.com your-email@example.com

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: sudo bash setup_ssl.sh your-domain.com your-email@example.com"
    exit 1
fi

echo "=========================================="
echo "SSL Certificate Setup"
echo "=========================================="
echo "Domain: $DOMAIN"
echo "Email: $EMAIL"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Create webroot directory for certbot
echo "[1/5] Creating webroot directory..."
mkdir -p /var/www/certbot
chown -R www-data:www-data /var/www/certbot

# Update nginx config with domain
echo "[2/5] Updating nginx configuration with domain..."
sed -i "s/your-domain.com/$DOMAIN/g" /etc/nginx/sites-available/llm-chess-coach
nginx -t && systemctl reload nginx

# Obtain SSL certificate
echo "[3/5] Obtaining SSL certificate from Let's Encrypt..."
certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"

# Update nginx config to enable SSL
echo "[4/5] Enabling SSL in nginx..."
nginx -t && systemctl reload nginx

# Set up auto-renewal
echo "[5/5] Setting up auto-renewal..."
cat > /etc/cron.d/certbot-renew << 'EOF'
# Renew Let's Encrypt certificates twice daily
0 0,12 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
EOF

echo ""
echo "=========================================="
echo "SSL Certificate Setup Complete!"
echo "=========================================="
echo "Your site is now available at: https://$DOMAIN"
echo ""
echo "Certificate auto-renewal is configured to run twice daily."
echo "To manually renew: certbot renew"
echo "To test renewal: certbot renew --dry-run"
echo "=========================================="
