#!/usr/bin/env bash
# Idempotent server setup — safe to run on every deploy.
# Called by deploy.sh; do not run manually.
set -euo pipefail

INSTALL_DIR="${1:-/opt/oa-pipeline}"
VENV="$INSTALL_DIR/venv"

echo "[bootstrap] INSTALL_DIR=$INSTALL_DIR"

# ── System packages ────────────────────────────────────────────────────────
if ! dpkg -s python3-venv &>/dev/null || ! dpkg -s nginx &>/dev/null || \
   ! dpkg -s libgomp1 &>/dev/null; then
    echo "[bootstrap] Installing system packages..."
    apt-get update -q
    apt-get install -y -q nginx python3-venv python3-full libgomp1
fi

# ── Directories ────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR/data/pipelines"
mkdir -p "$INSTALL_DIR/data"

# ── Stop any conflicting old services ─────────────────────────────────────
for OLD_SVC in openalex-dashboard openalex_dashboard; do
    if systemctl list-unit-files --quiet "$OLD_SVC.service" 2>/dev/null | grep -q "$OLD_SVC"; then
        echo "[bootstrap] Stopping old service: $OLD_SVC"
        systemctl stop "$OLD_SVC" 2>/dev/null || true
        systemctl disable "$OLD_SVC" 2>/dev/null || true
    fi
done

# ── Python venv ────────────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/python3" ]; then
    echo "[bootstrap] Creating venv..."
    python3 -m venv "$VENV"
fi
echo "[bootstrap] Syncing Python packages..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR"

# ── Secret key (generated once, never overwritten) ─────────────────────────
SECRET_KEY_FILE="$INSTALL_DIR/data/.secret_key"
if [ ! -f "$SECRET_KEY_FILE" ]; then
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$SECRET_KEY_FILE"
    chmod 600 "$SECRET_KEY_FILE"
fi
SECRET_KEY=$(cat "$SECRET_KEY_FILE")

# ── systemd service ────────────────────────────────────────────────────────
echo "[bootstrap] Writing systemd service..."
cat > /etc/systemd/system/oa-pipeline.service << EOF
[Unit]
Description=OA Pipeline (FastAPI)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV/bin/uvicorn api.main:app \
    --host 127.0.0.1 --port 8000 --workers 1 \
    --no-access-log
Restart=on-failure
RestartSec=5
Environment=SECRET_KEY=$SECRET_KEY
Environment=PIPELINES_BASE_DIR=$INSTALL_DIR/data/pipelines
Environment=META_DB_PATH=$INSTALL_DIR/data/dashboard.db
Environment=GZ_FILES_BASE_DIR=/root/data/openalex/works
EnvironmentFile=-$INSTALL_DIR/data/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable oa-pipeline

# ── nginx ──────────────────────────────────────────────────────────────────
echo "[bootstrap] Configuring nginx..."
rm -f /etc/nginx/sites-enabled/*
cp "$INSTALL_DIR/deploy/nginx.conf" /etc/nginx/sites-available/oa-pipeline
ln -sf /etc/nginx/sites-available/oa-pipeline /etc/nginx/sites-enabled/oa-pipeline
nginx -t
systemctl enable nginx
systemctl reload nginx

echo "[bootstrap] Done."
