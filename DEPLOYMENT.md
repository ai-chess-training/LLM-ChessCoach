# LLM Chess Coach - VPS Deployment Guide

This guide walks you through deploying the LLM Chess Coach application to an Ubuntu VPS (such as OVHCloud, DigitalOcean, Linode, etc.).

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial VPS Setup](#initial-vps-setup)
3. [Application Installation](#application-installation)
4. [Configuration](#configuration)
5. [SSL Certificate Setup](#ssl-certificate-setup)
6. [Starting the Application](#starting-the-application)
7. [Monitoring and Maintenance](#monitoring-and-maintenance)
8. [Troubleshooting](#troubleshooting)
9. [Security Checklist](#security-checklist)

## Prerequisites

### Server Requirements
- **OS**: Ubuntu 20.04+ (64-bit)
- **RAM**: Minimum 2GB (4GB+ recommended)
- **CPU**: 2+ cores recommended
- **Disk**: 20GB+ available space
- **Domain**: (Optional but recommended) A domain name pointing to your VPS IP

### What You'll Need
- SSH access to your VPS with root or sudo privileges
- An OpenAI API key (for LLM coaching features)
- A secure API key for your application (generate with: `openssl rand -hex 32`)

## Initial VPS Setup

### 1. Connect to Your VPS

```bash
ssh root@your-vps-ip
```

### 2. Update System Packages

```bash
apt update && apt upgrade -y
```

### 3. Create a Non-Root User (if not already done)

```bash
adduser yourusername
usermod -aG sudo yourusername
```

### 4. Set Up SSH Key Authentication

On your local machine:
```bash
ssh-copy-id yourusername@your-vps-ip
```

## Application Installation

### 1. Clone the Repository

```bash
cd /opt
sudo git clone https://github.com/your-username/LLM-ChessCoach.git llm-chess-coach
cd llm-chess-coach
```

### 2. Run the Automated Setup Script

```bash
sudo bash scripts/setup_ubuntu_vps.sh
```

This script will:
- Install all required system packages (Python, nginx, stockfish, etc.)
- Create the application user (`chesscoach`)
- Set up the directory structure
- Create a Python virtual environment
- Configure nginx
- Set up the firewall (UFW)
- Install and configure the systemd service

### 3. Install Python Dependencies

```bash
cd /opt/llm-chess-coach
sudo -u chesscoach venv/bin/pip install -r requirements.txt
```

## Configuration

### 1. Create Environment File

```bash
cd /opt/llm-chess-coach
sudo cp .env.example .env
sudo chown chesscoach:chesscoach .env
sudo chmod 600 .env
```

### 2. Configure Environment Variables

Edit the `.env` file with your settings:

```bash
sudo nano .env
```

**Required settings:**

```bash
# Set to production
ENVIRONMENT=production

# Generate a secure API key: openssl rand -hex 32
API_KEY=your-generated-secure-key-here

# Your OpenAI API key
OPENAI_API_KEY=sk-your-openai-key-here

# Your domain (if applicable)
ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Stockfish path (Ubuntu default)
STOCKFISH_PATH=engines/stockfish
```

**Optional settings:**

```bash
# Adjust analysis depth (higher = slower, more accurate)
MULTIPV=5
NODES_PER_PV=1000000

# Logging
LOG_LEVEL=INFO

# Number of workers (default: auto-detected)
GUNICORN_WORKERS=4
```

### 3. Update Nginx Configuration

Edit the nginx configuration to use your domain:

```bash
sudo nano /etc/nginx/sites-available/llm-chess-coach
```

Replace `your-domain.com` with your actual domain name.

Test nginx configuration:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## SSL Certificate Setup

### Option 1: With a Domain Name (Recommended)

```bash
sudo bash scripts/setup_ssl.sh yourdomain.com your-email@example.com
```

This script will:
- Obtain an SSL certificate from Let's Encrypt
- Configure auto-renewal
- Update nginx to use HTTPS

### Option 2: Without a Domain (Development/Testing Only)

You can access the application via HTTP on port 80, but this is **not recommended for production**.

## Starting the Application

### 1. Start the Service

```bash
sudo systemctl start llm-chess-coach.service
```

### 2. Check Service Status

```bash
sudo systemctl status llm-chess-coach.service
```

You should see `active (running)` in green.

### 3. Enable Auto-Start on Boot

```bash
sudo systemctl enable llm-chess-coach.service
```

### 4. View Logs

```bash
# Follow live logs
sudo journalctl -u llm-chess-coach.service -f

# View recent logs
sudo journalctl -u llm-chess-coach.service -n 100

# Application logs
sudo tail -f /opt/llm-chess-coach/logs/app.log
```

## Server Hardening

Run the security hardening script:

```bash
sudo bash scripts/harden_server.sh
```

This will:
- Harden SSH configuration
- Configure fail2ban for intrusion prevention
- Set up automatic security updates
- Disable unnecessary services
- Configure kernel security parameters
- Set secure file permissions

**IMPORTANT**: Before running this script, ensure you have:
1. Set up SSH key authentication
2. Tested your SSH key login
3. Noted your current SSH port (if changed)

## Monitoring and Maintenance

### Health Checks

The application provides health check endpoints:

```bash
# Basic health check
curl https://yourdomain.com/health

# Readiness check (validates dependencies)
curl https://yourdomain.com/ready
```

### Service Management

```bash
# Restart the application
sudo systemctl restart llm-chess-coach.service

# Stop the application
sudo systemctl stop llm-chess-coach.service

# View service logs
sudo journalctl -u llm-chess-coach.service -f
```

### Nginx Management

```bash
# Restart nginx
sudo systemctl restart nginx

# Test nginx configuration
sudo nginx -t

# View nginx logs
sudo tail -f /var/log/nginx/llm-chess-coach-access.log
sudo tail -f /var/log/nginx/llm-chess-coach-error.log
```

### Backups

Create a backup:

```bash
sudo bash /opt/llm-chess-coach/scripts/backup.sh
```

Backups are stored in `/var/backups/llm-chess-coach/` and automatically cleaned up after 30 days.

### Updates and Deployment

To deploy updates:

```bash
cd /opt/llm-chess-coach
sudo bash scripts/deploy.sh
```

This script will:
1. Pull latest code from git
2. Install/update dependencies
3. Restart the service
4. Verify the service is running

### Log Rotation

Logs are automatically rotated daily and compressed. Configuration is in:
- `/etc/logrotate.d/llm-chess-coach` (copy from `logrotate/llm-chess-coach`)

Install the log rotation config:

```bash
sudo cp /opt/llm-chess-coach/logrotate/llm-chess-coach /etc/logrotate.d/
```

## Troubleshooting

### Service Won't Start

1. Check logs:
   ```bash
   sudo journalctl -u llm-chess-coach.service -n 50
   ```

2. Verify environment variables:
   ```bash
   sudo -u chesscoach cat /opt/llm-chess-coach/.env
   ```

3. Test manually:
   ```bash
   sudo -u chesscoach /opt/llm-chess-coach/venv/bin/python /opt/llm-chess-coach/api_server.py
   ```

### "Stockfish not found" Error

Verify Stockfish installation:

```bash
which stockfish
# or
which /usr/games/stockfish
```

If not found:

```bash
sudo apt install stockfish
```

Update STOCKFISH_PATH in `.env`:

```bash
STOCKFISH_PATH=engines/stockfish
```

### Permission Errors

Fix file ownership:

```bash
sudo chown -R chesscoach:chesscoach /opt/llm-chess-coach
sudo chmod 750 /opt/llm-chess-coach
sudo chmod 640 /opt/llm-chess-coach/.env
```

### High Memory Usage

Reduce Gunicorn workers in `.env`:

```bash
GUNICORN_WORKERS=2
```

Or reduce analysis depth:

```bash
NODES_PER_PV=500000
MULTIPV=3
```

### SSL Certificate Issues

Check certificate status:

```bash
sudo certbot certificates
```

Renew manually:

```bash
sudo certbot renew
```

Test renewal:

```bash
sudo certbot renew --dry-run
```

### 502 Bad Gateway

1. Check if application is running:
   ```bash
   sudo systemctl status llm-chess-coach.service
   ```

2. Check socket file:
   ```bash
   ls -l /opt/llm-chess-coach/llm-chess-coach.sock
   ```

3. Check nginx error logs:
   ```bash
   sudo tail -f /var/log/nginx/llm-chess-coach-error.log
   ```

## Security Checklist

Before going live, verify:

- [ ] `ENVIRONMENT=production` in `.env`
- [ ] Strong, unique `API_KEY` generated and set
- [ ] `ALLOWED_ORIGINS` restricted to your domain(s)
- [ ] SSL certificate installed and working
- [ ] Firewall (UFW) enabled with only necessary ports open
- [ ] SSH configured for key-only authentication
- [ ] Root login disabled in SSH
- [ ] fail2ban installed and running
- [ ] Automatic security updates enabled
- [ ] Regular backups configured
- [ ] Application logs monitored
- [ ] Health checks configured in monitoring
- [ ] `.env` file permissions set to 600
- [ ] Sensitive data not in git repository

## Testing Your Deployment

### 1. Health Check

```bash
curl https://yourdomain.com/health
# Expected: {"status":"healthy","timestamp":...}
```

### 2. Create a Session

```bash
curl -X POST "https://yourdomain.com/v1/sessions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json"
```

### 3. Check SSL

```bash
curl -I https://yourdomain.com
# Look for "Strict-Transport-Security" header
```

### 4. Verify Security Headers

```bash
curl -I https://yourdomain.com/health
# Should see X-Content-Type-Options, X-Frame-Options, etc.
```

## Support and Additional Resources

- **Application Logs**: `/opt/llm-chess-coach/logs/`
- **Nginx Logs**: `/var/log/nginx/llm-chess-coach-*.log`
- **System Logs**: `sudo journalctl -xe`
- **Security Monitoring**: `sudo bash /opt/monitor_security.sh`
- **GitHub Issues**: https://github.com/your-username/LLM-ChessCoach/issues

## Performance Tuning

### For High Traffic

1. Increase Gunicorn workers:
   ```bash
   GUNICORN_WORKERS=8
   ```

2. Add nginx caching (edit `/etc/nginx/sites-available/llm-chess-coach`):
   ```nginx
   proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=api_cache:10m max_size=1g;
   proxy_cache api_cache;
   proxy_cache_valid 200 5m;
   ```

3. Consider using Redis for session storage (future enhancement)

### For Resource-Constrained Servers

1. Reduce workers:
   ```bash
   GUNICORN_WORKERS=2
   ```

2. Reduce analysis depth:
   ```bash
   NODES_PER_PV=250000
   MULTIPV=3
   ```

3. Enable swap if needed:
   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

---

**Congratulations!** Your LLM Chess Coach is now deployed and ready to use. Remember to keep your system updated and monitor logs regularly.
