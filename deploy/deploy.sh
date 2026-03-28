#!/usr/bin/env bash
# Deploy updated code to a remote Ubuntu server.
# Usage:
#   ./deploy/deploy.sh user@hostname
#   ./deploy/deploy.sh user@hostname --restart-only
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
    echo "[1/2] Syncing dashboard..."
    rsync -az --delete --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' \
        "$REPO_ROOT/dashboard/" "$HOST:$INSTALL_DIR/dashboard/"

    echo "[1/2] Syncing pipeline scripts..."
    rsync -az --delete --exclude='__pycache__/' --exclude='*.pyc' \
        "$REPO_ROOT/pipeline/" "$HOST:$INSTALL_DIR/pipeline/"
fi

echo "[2/2] Restarting service..."
ssh "$HOST" "systemctl restart oa-pipeline && systemctl status oa-pipeline --no-pager -l"

echo ""
echo "=== Deploy complete! ==="
echo "Dashboard: http://$(ssh "$HOST" 'hostname -I | awk "{print \$1}"')/"
