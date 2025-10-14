#!/bin/bash
set -e

# Server Security Hardening Script
# Run this after initial setup to enhance security

echo "=========================================="
echo "Server Security Hardening"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

# Backup SSH config
echo "[1/10] Backing up SSH configuration..."
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup.$(date +%Y%m%d)

# Harden SSH configuration
echo "[2/10] Hardening SSH configuration..."
cat >> /etc/ssh/sshd_config << 'EOF'

# Security Hardening
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding no
PrintMotd no
AcceptEnv LANG LC_*
ClientAliveInterval 300
ClientAliveCountMax 2
MaxAuthTries 3
MaxSessions 2
Protocol 2
EOF

# Restart SSH (be careful!)
echo "SSH configuration updated. Restart SSH? (y/n)"
read -r RESTART_SSH
if [ "$RESTART_SSH" = "y" ]; then
    systemctl restart sshd
    echo "SSH restarted"
fi

# Configure fail2ban
echo "[3/10] Configuring fail2ban..."
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
destemail = root@localhost
sendername = Fail2Ban
action = %(action_mw)s

[sshd]
enabled = true
port = ssh
logpath = %(sshd_log)s
maxretry = 3

[nginx-limit-req]
enabled = true
filter = nginx-limit-req
port = http,https
logpath = /var/log/nginx/*error.log
maxretry = 5

[nginx-noscript]
enabled = true
port = http,https
filter = nginx-noscript
logpath = /var/log/nginx/*access.log
maxretry = 6

[nginx-badbots]
enabled = true
port = http,https
filter = nginx-badbots
logpath = /var/log/nginx/*access.log
maxretry = 2
EOF

systemctl enable fail2ban
systemctl restart fail2ban
echo "Fail2ban configured"

# Set up automatic security updates
echo "[4/10] Configuring automatic security updates..."
apt-get install -y unattended-upgrades apt-listchanges
dpkg-reconfigure -plow unattended-upgrades

cat > /etc/apt/apt.conf.d/50unattended-upgrades << 'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

# Disable unnecessary services
echo "[5/10] Disabling unnecessary services..."
for service in bluetooth avahi-daemon cups; do
    if systemctl is-enabled --quiet $service 2>/dev/null; then
        systemctl disable $service
        systemctl stop $service
        echo "Disabled $service"
    fi
done

# Configure kernel parameters for security
echo "[6/10] Configuring kernel parameters..."
cat >> /etc/sysctl.conf << 'EOF'

# Security hardening
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.tcp_syncookies = 1
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
fs.suid_dumpable = 0
EOF

sysctl -p
echo "Kernel parameters configured"

# Set file permissions
echo "[7/10] Setting secure file permissions..."
chmod 644 /etc/passwd
chmod 644 /etc/group
chmod 600 /etc/shadow
chmod 600 /etc/gshadow
chmod 644 /etc/ssh/sshd_config

# Configure system limits
echo "[8/10] Configuring system limits..."
cat >> /etc/security/limits.conf << 'EOF'

# Application limits
chesscoach soft nofile 65536
chesscoach hard nofile 65536
chesscoach soft nproc 512
chesscoach hard nproc 512
EOF

# Install and configure auditd for monitoring
echo "[9/10] Installing audit daemon..."
apt-get install -y auditd audispd-plugins

# Create a monitoring script
echo "[10/10] Creating monitoring script..."
cat > /opt/monitor_security.sh << 'EOF'
#!/bin/bash
# Security monitoring script

echo "=== Failed Login Attempts ==="
grep "Failed password" /var/log/auth.log | tail -10

echo ""
echo "=== Fail2ban Status ==="
fail2ban-client status

echo ""
echo "=== Last 10 sudo Commands ==="
grep sudo /var/log/auth.log | tail -10

echo ""
echo "=== Open Connections ==="
ss -tunap | grep ESTABLISHED

echo ""
echo "=== Disk Usage ==="
df -h | grep -v tmpfs

echo ""
echo "=== Memory Usage ==="
free -h
EOF

chmod +x /opt/monitor_security.sh

echo ""
echo "=========================================="
echo "Server Hardening Complete!"
echo "=========================================="
echo ""
echo "Security Checklist:"
echo "  [✓] SSH hardened (root login disabled, password auth disabled)"
echo "  [✓] Fail2ban configured for SSH and nginx"
echo "  [✓] Automatic security updates enabled"
echo "  [✓] Unnecessary services disabled"
echo "  [✓] Kernel security parameters configured"
echo "  [✓] File permissions secured"
echo "  [✓] System limits configured"
echo "  [✓] Audit daemon installed"
echo ""
echo "Additional recommendations:"
echo "  1. Set up SSH key-only authentication before restarting SSH"
echo "  2. Review fail2ban logs: fail2ban-client status"
echo "  3. Run security monitoring: bash /opt/monitor_security.sh"
echo "  4. Keep system updated: apt update && apt upgrade"
echo "  5. Review logs regularly: journalctl -xe"
echo ""
echo "=========================================="
