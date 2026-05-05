#!/usr/bin/env bash
# ── AskMojo — First-Time Production Deploy Script ────────────────────────────
# Run on Ubuntu 24.04 LTS as root or a sudo user.
# Usage: chmod +x deploy.sh && sudo ./deploy.sh

set -euo pipefail

echo "=== [1/7] Installing Docker ==="
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release nginx
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
echo "Docker $(docker --version)"

echo ""
echo "=== [2/7] Disabling Swap (hard constraint) ==="
swapoff -a
sed -i '/\bswap\b/d' /etc/fstab
echo "Swap disabled."

echo ""
echo "=== [3/7] Setting kernel memory overcommit policy ==="
# Prevent OOM killer from being triggered unexpectedly
sysctl -w vm.overcommit_memory=1
echo "vm.overcommit_memory=1" >> /etc/sysctl.d/99-askmojo.conf
sysctl -p /etc/sysctl.d/99-askmojo.conf

echo ""
echo "=== [4/7] Installing Nginx config ==="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/nginx.prod.conf" /etc/nginx/sites-available/askmojo
ln -sf /etc/nginx/sites-available/askmojo /etc/nginx/sites-enabled/askmojo
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "Nginx configured."

echo ""
echo "=== [5/7] Setting up log rotation ==="
cat > /etc/logrotate.d/askmojo << 'EOF'
/var/log/askmojo/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    maxsize 50M
    create 0640 root root
}
EOF
mkdir -p /var/log/askmojo
echo "Log rotation configured."

echo ""
echo "=== [6/7] Building Docker image ==="
cd "${SCRIPT_DIR}"
docker compose build --no-cache

echo ""
echo "=== [7/7] Starting containers ==="
docker compose up -d

echo ""
echo "Waiting 30s for startup..."
sleep 30

echo ""
echo "=== Health Check ==="
curl -sf http://127.0.0.1:8004/api/health | python3 -m json.tool \
  && echo "" && echo "✅ Deploy successful!" \
  || echo "⚠️  Health check failed — check: docker compose logs"

echo ""
echo "=== Resource Usage ==="
docker stats --no-stream askmojo_backend
