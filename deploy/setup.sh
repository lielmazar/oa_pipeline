#!/usr/bin/env bash
# First-time server setup for OpenAlex Pipeline Dashboard
# Run from the repo root on the target Ubuntu server:
#   bash deploy/setup.sh
set -euo pipefail

INSTALL_DIR="/opt/oa-pipeline"
DATA_DIR="$INSTALL_DIR/data"
PIPELINES_DIR="$DATA_DIR/pipelines"
VENV="$INSTALL_DIR/venv"

echo "=== OpenAlex Pipeline — First-time Setup ==="

# ── System packages ───────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
apt-get update -q
apt-get install -y -q nginx python3-venv python3-full rsync git

# ── Directories ───────────────────────────────────────────────────────────────
echo "[2/6] Creating directories..."
mkdir -p "$INSTALL_DIR/dashboard"
mkdir -p "$INSTALL_DIR/pipeline"
mkdir -p "$PIPELINES_DIR"
mkdir -p "$DATA_DIR"

# ── Python virtualenv ─────────────────────────────────────────────────────────
echo "[3/6] Creating Python virtualenv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install flask gunicorn werkzeug -q
# Optional (faster JSON parsing in pipeline scripts):
"$VENV/bin/pip" install orjson --no-deps -q 2>/dev/null || true

# ── systemd service ───────────────────────────────────────────────────────────
echo "[4/6] Installing systemd service..."

# Generate a secret key if one doesn't exist yet
SECRET_KEY_FILE="$DATA_DIR/.secret_key"
if [ ! -f "$SECRET_KEY_FILE" ]; then
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$SECRET_KEY_FILE"
    chmod 600 "$SECRET_KEY_FILE"
fi
SECRET_KEY=$(cat "$SECRET_KEY_FILE")

# Write the service file with the generated secret key
cat > /etc/systemd/system/oa-pipeline.service <<EOF
[Unit]
Description=OpenAlex Pipeline Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/dashboard
ExecStart=$VENV/bin/python3 -m gunicorn \\
    -w 1 -k gthread --threads 16 \\
    -b 127.0.0.1:5000 --timeout 0 \\
    --log-level info \\
    "app:create_app()"
Restart=on-failure
RestartSec=5
Environment=SECRET_KEY=$SECRET_KEY
Environment=PIPELINES_BASE_DIR=$PIPELINES_DIR
Environment=META_DB_PATH=$DATA_DIR/dashboard.db
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable oa-pipeline

# ── nginx ─────────────────────────────────────────────────────────────────────
echo "[5/6] Configuring nginx..."
cp "$(dirname "$0")/nginx.conf" /etc/nginx/sites-available/oa-pipeline
ln -sf /etc/nginx/sites-available/oa-pipeline /etc/nginx/sites-enabled/oa-pipeline
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl reload nginx

# ── First deploy ──────────────────────────────────────────────────────────────
echo "[6/6] Copying application files..."
rsync -a --delete "$(dirname "$0")/../dashboard/" "$INSTALL_DIR/dashboard/"
rsync -a --delete "$(dirname "$0")/../pipeline/"  "$INSTALL_DIR/pipeline/"

# Create venv symlink inside dashboard so start.sh can find it
ln -sfn "$VENV" "$INSTALL_DIR/dashboard/venv"

systemctl start oa-pipeline

echo ""
echo "=== Setup complete! ==="
echo "Dashboard: http://$(hostname -I | awk '{print $1}')/"
echo ""
echo "Next step — create your first admin user:"
echo "  cd $INSTALL_DIR/dashboard && $VENV/bin/python3 manage.py create-user admin"
