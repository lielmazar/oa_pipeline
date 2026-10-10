#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 EC2 instance to run the pipeline.
# Run as the default user (ubuntu) after cloning the repo:
#     bash deploy/ec2_bootstrap.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH=/mnt/scratch

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip tmux nvme-cli

# Use the instance-store NVMe disk (c6id/m6id/r6id families) as scratch space, if present.
# Instance store is wiped on stop — fine for scratch, the outputs live in S3.
if [ ! -d "$SCRATCH" ]; then
  DEV=$(lsblk -dpno NAME,MODEL | awk '/Instance Storage/ {print $1; exit}')
  if [ -n "${DEV:-}" ]; then
    sudo mkfs.ext4 -F "$DEV"
    sudo mkdir -p "$SCRATCH"
    sudo mount "$DEV" "$SCRATCH"
    sudo chown "$USER" "$SCRATCH"
    echo "Mounted $DEV at $SCRATCH"
  else
    sudo mkdir -p "$SCRATCH" && sudo chown "$USER" "$SCRATCH"
    echo "No instance-store disk found; $SCRATCH is on the root volume (size it >= 100 GB)."
  fi
fi

cd "$REPO_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"

# Pre-install DuckDB's S3 extensions so reports work without fetching at run time.
.venv/bin/python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs; INSTALL aws')"

cat <<EOF

Ready. Next:
  1. Edit config.yaml: storage_root (your bucket) and tmp_dir: $SCRATCH
  2. source .venv/bin/activate && python -m pipeline.check_aws      # preflight
  3. tmux new -s oa      (then: source .venv/bin/activate again inside tmux)
     python -m pipeline.run_all --smoke --run-id smoke     # ~minutes, end-to-end check
     python -m pipeline.run_all                            # full run
EOF
