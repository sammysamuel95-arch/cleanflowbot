#!/bin/bash
# collector/setup_vps.sh — Fresh Vultr Ubuntu 22.04 VPS setup
# Run as root after first login.
# Usage: bash setup_vps.sh
set -e

# Usage: bash setup_vps.sh <redis_password>
REDIS_PASS="${1:-}"
if [ -z "$REDIS_PASS" ]; then
    echo "ERROR: Redis password required as first argument"
    echo "Usage: bash setup_vps.sh <redis_password>"
    exit 1
fi

echo "=== CleanFlowBot VPS Collector Setup ==="
echo ""

# ── 1. System ─────────────────────────────────────────────────────────────
echo "[1] Updating system..."
DEBIAN_FRONTEND=noninteractive apt update && DEBIAN_FRONTEND=noninteractive apt upgrade -y
apt install -y python3 python3-pip redis-server git curl screen

# ── 2. Redis ──────────────────────────────────────────────────────────────
echo "[2] Configuring Redis..."

# Backup original config
cp /etc/redis/redis.conf /etc/redis/redis.conf.bak

# Append collector settings
cat >> /etc/redis/redis.conf << EOF

# === CleanFlowBot VPS settings ===
bind 0.0.0.0
requirepass ${REDIS_PASS}
maxmemory 256mb
maxmemory-policy allkeys-lru
# Disable persistence — data is ephemeral (re-fetched on restart)
save ""
appendonly no
EOF

systemctl restart redis
echo "[2] Redis restarted with password protection"

# Verify Redis is up
redis-cli -a "${REDIS_PASS}" ping && echo "[2] Redis PING OK" || echo "[2] WARNING: Redis PING failed"

# ── 3. Python deps ────────────────────────────────────────────────────────
echo "[3] Installing Python packages..."
pip3 install --break-system-packages \
    redis \
    aiohttp \
    thefuzz \
    python-dotenv \
    curl_cffi \
    playwright \
    yarl

python3 -m playwright install chromium
echo "[3] Python packages installed"

# ── 4. Project directories ────────────────────────────────────────────────
echo "[4] Creating project structure..."
mkdir -p /opt/cleanflowbot/collector
mkdir -p /opt/cleanflowbot/data
mkdir -p /opt/cleanflowbot/feeds
mkdir -p /opt/cleanflowbot/core
mkdir -p /opt/cleanflowbot/matching
mkdir -p /opt/cleanflowbot/tools
mkdir -p /opt/cleanflowbot/docs

echo "[4] Done"

# ── 5. VPS firewall ───────────────────────────────────────────────────────
echo "[5] Firewall: allow SSH + Redis from trusted IPs only"
echo "    NOTE: You MUST add your Mac IP manually:"
echo "    ufw allow from YOUR_MAC_IP to any port 6379"
echo "    ufw allow ssh"
echo "    ufw enable"

# ── 6. Summary ────────────────────────────────────────────────────────────
echo ""
echo "=== Setup done ==="
echo ""
echo "Next steps:"
echo "  1. Open Redis port to your Mac IP only:"
echo "     ufw allow from <MAC_IP> to any port 6379"
echo "     ufw allow ssh && ufw enable"
echo ""
echo "  2. Copy files from Mac to VPS:"
echo "     scp -r collector/ feeds/ core/ matching/ config.py root@VPS_IP:/opt/cleanflowbot/"
echo "     scp data/cookie.json data/session.json data/secrets.env data/bot_config.json root@VPS_IP:/opt/cleanflowbot/data/"
echo ""
echo "  3. Optional — copy Steam Playwright profile (Approach A):"
echo "     scp -r data/playwright_etop_profile/ root@VPS_IP:/opt/cleanflowbot/data/"
echo ""
echo "  4. Start collector (use screen to keep alive):"
echo "     screen -S collector"
echo "     cd /opt/cleanflowbot"
echo "     python3 collector/collector_main.py --redis-pass '${REDIS_PASS}' --etop-interval 1"
echo "     Ctrl+A D  (detach screen)"
echo ""
echo "  5. From Mac, test connection:"
echo "     python3 tools/redis_reader_test.py --host VPS_IP --password '${REDIS_PASS}'"
echo ""
echo "Redis password saved — store it safely: ${REDIS_PASS}"
