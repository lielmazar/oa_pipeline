#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/venv"
if [ ! -f "$VENV/bin/python3" ]; then
    echo "ERROR: virtualenv not found at $VENV"
    echo "Run: python3 -m venv $VENV && $VENV/bin/pip install flask gunicorn"
    exit 1
fi

if [ -z "$SECRET_KEY" ]; then
    export SECRET_KEY="$("$VENV/bin/python3" -c 'import secrets; print(secrets.token_hex(32))')"
fi

echo "Starting OpenAlex Pipeline Dashboard on http://127.0.0.1:5000"

exec "$VENV/bin/python3" -m gunicorn \
    -w 1 \
    -k gthread \
    --threads 16 \
    -b 127.0.0.1:5000 \
    --timeout 0 \
    --log-level info \
    "app:create_app()"
