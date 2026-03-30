#!/usr/bin/env bash
# One-command deploy from your local machine (Windows Git Bash, WSL, or Linux).
# Handles both first-time setup and routine code updates — safe to run repeatedly.
#
# Usage:
#   bash deploy/deploy.sh user@hostname
#   bash deploy/deploy.sh user@hostname --restart-only
#
# Requires SSH key auth (password auth does not work with rsync in Git Bash).
# Set up keys once:
#   ssh-keygen -t ed25519          # skip if you already have a key
#   ssh-copy-id user@hostname
set -euo pipefail

HOST="${1:-}"
if [ -z "$HOST" ]; then
    echo "Usage: $0 user@hostname [--restart-only]"
    exit 1
fi

RESTART_ONLY="${2:-}"
INSTALL_DIR="/opt/oa-pipeline"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Deploying to $HOST ==="

if [ "$RESTART_ONLY" != "--restart-only" ]; then
    echo "[1/4] Syncing dashboard..."
    rsync -az --delete \
        --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
        "$REPO_ROOT/dashboard/" "$HOST:$INSTALL_DIR/dashboard/"

    echo "[2/4] Syncing pipeline scripts..."
    rsync -az --delete \
        --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
        "$REPO_ROOT/pipeline/" "$HOST:$INSTALL_DIR/pipeline/"

    echo "[3/4] Syncing deploy scripts..."
    rsync -az \
        "$REPO_ROOT/deploy/" "$HOST:$INSTALL_DIR/deploy/"

    echo "[4/4] Bootstrapping server (venv, systemd, nginx)..."
    # Strip Windows line endings (\r) in case the script was checked out on Windows
    ssh "$HOST" "sed -i 's/\r//' $INSTALL_DIR/deploy/server_bootstrap.sh && bash $INSTALL_DIR/deploy/server_bootstrap.sh $INSTALL_DIR"
fi

echo "Restarting service..."
ssh "$HOST" "systemctl restart oa-pipeline && systemctl status oa-pipeline --no-pager -l"

echo ""
echo "=== Deploy complete! ==="
echo "Dashboard: http://$(ssh "$HOST" 'hostname -I | awk "{print \$1}"')/"
echo ""
echo "First deploy? Create an admin user:"
echo "  ssh $HOST '/opt/oa-pipeline/venv/bin/python3 /opt/oa-pipeline/dashboard/manage.py create-user admin'"
