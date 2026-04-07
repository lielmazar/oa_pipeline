#!/usr/bin/env bash
# One-command deploy from your local machine (Windows Git Bash, WSL, or Linux).
# Handles both first-time setup and routine code updates — safe to run repeatedly.
#
# Usage:
#   bash deploy/deploy.sh user@hostname
#   bash deploy/deploy.sh user@hostname --restart-only
#   bash deploy/deploy.sh user@hostname --skip-build   # skip npm build
#
# Requires SSH key auth:
#   ssh-keygen -t ed25519
#   ssh-copy-id user@hostname
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@hostname [--restart-only|--skip-build]"
    exit 1
fi

FLAG="${2:-}"
INSTALL_DIR="/opt/oa-pipeline"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Deploying to $HOST ==="

if [ "$FLAG" = "--restart-only" ]; then
    echo "Restarting service (no code sync)..."
    ssh "$HOST" "systemctl restart oa-pipeline && systemctl status oa-pipeline --no-pager -l"
    echo "=== Done ==="
    exit 0
fi

# ── 1. Build React frontend ────────────────────────────────────────────────
if [ "$FLAG" != "--skip-build" ]; then
    echo "[1/5] Building React frontend..."
    cd "$REPO_ROOT/web"
    npm ci --silent
    npm run build
    cd "$REPO_ROOT"
    # Copy built assets into api/static/ (FastAPI serves from here)
    rm -rf "$REPO_ROOT/api/static"
    cp -r "$REPO_ROOT/web/dist" "$REPO_ROOT/api/static"
    echo "      → built to api/static/"
fi

# ── 2. Sync Python backend ─────────────────────────────────────────────────
echo "[2/5] Syncing Python backend (api/ + pipeline/)..."
rsync -az --delete \
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
    --exclude='static/'  \
    "$REPO_ROOT/api/" "$HOST:$INSTALL_DIR/api/"

# Sync static (built frontend) separately — no --delete to preserve other static files
rsync -az \
    "$REPO_ROOT/api/static/" "$HOST:$INSTALL_DIR/api/static/"

echo "[3/5] Syncing pipeline scripts..."
rsync -az --delete \
    --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
    "$REPO_ROOT/pipeline/" "$HOST:$INSTALL_DIR/pipeline/"

# ── 3. Sync project metadata ───────────────────────────────────────────────
echo "[4/5] Syncing project config..."
rsync -az \
    "$REPO_ROOT/pyproject.toml" "$HOST:$INSTALL_DIR/pyproject.toml"
rsync -az \
    "$REPO_ROOT/deploy/" "$HOST:$INSTALL_DIR/deploy/"

# ── 4. Bootstrap server ────────────────────────────────────────────────────
echo "[5/5] Bootstrapping server (venv, systemd, nginx)..."
ssh "$HOST" "sed -i 's/\r//' $INSTALL_DIR/deploy/server_bootstrap.sh && bash $INSTALL_DIR/deploy/server_bootstrap.sh $INSTALL_DIR"

# ── 5. Restart ────────────────────────────────────────────────────────────
echo "Restarting service..."
ssh "$HOST" "systemctl restart oa-pipeline && systemctl status oa-pipeline --no-pager -l"

echo ""
echo "=== Deploy complete! ==="
echo "App: http://$(ssh "$HOST" 'hostname -I | awk "{print \$1}"')/"
echo ""
echo "First deploy? Create an admin user:"
echo "  ssh $HOST '$INSTALL_DIR/venv/bin/python3 -c \"from api.db.meta import init_db; init_db()\"'"
echo "  # Then use the /api/auth/register endpoint or add user via manage script"
