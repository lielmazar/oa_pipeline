<#
.SYNOPSIS
    Deploy OA Pipeline to Ubuntu server from PowerShell.

.DESCRIPTION
    Syncs code and bootstraps the server (venv, systemd, nginx).
    Safe to run for both first-time setup and routine updates.
    Requires SSH key auth — see README for one-time setup.

.PARAMETER Target
    SSH target in user@host format, e.g. root@157.180.11.56

.PARAMETER RestartOnly
    Skip file sync; just restart the systemd service.

.EXAMPLE
    .\deploy\deploy.ps1 root@157.180.11.56
    .\deploy\deploy.ps1 root@157.180.11.56 -RestartOnly
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Target,
    [switch]$RestartOnly
)

$ErrorActionPreference = "Stop"
$INSTALL_DIR = "/opt/oa-pipeline"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot

# SSH/SCP options: timeout quickly on connect failures, keep connection alive during long transfers
$SSH_OPTS = @("-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3")

function Run-SSH {
    param([string]$Cmd)
    ssh @SSH_OPTS $Target $Cmd
}

function Run-SCP {
    param([string]$Src, [string]$Dest)
    scp @SSH_OPTS -r $Src "${Target}:${Dest}"
}

Write-Host "=== Deploying to $Target ===" -ForegroundColor Cyan

if (-not $RestartOnly) {
    # Ensure the install dir and persistent data dirs exist before anything else
    Write-Host "[1/5] Preparing remote directories..." -ForegroundColor Green
    Run-SSH "mkdir -p ${INSTALL_DIR}/data/pipelines"

    # scp nests src inside dest when dest already exists — remove first to get a clean copy
    Write-Host "[2/5] Syncing dashboard..." -ForegroundColor Green
    Run-SSH "rm -rf ${INSTALL_DIR}/dashboard"
    Run-SCP "$REPO_ROOT\dashboard" "${INSTALL_DIR}/"

    Write-Host "[3/5] Syncing pipeline scripts..." -ForegroundColor Green
    Run-SSH "rm -rf ${INSTALL_DIR}/pipeline"
    Run-SCP "$REPO_ROOT\pipeline" "${INSTALL_DIR}/"

    Write-Host "[4/5] Syncing deploy scripts..." -ForegroundColor Green
    Run-SSH "rm -rf ${INSTALL_DIR}/deploy"
    Run-SCP "$REPO_ROOT\deploy" "${INSTALL_DIR}/"

    Write-Host "[5/5] Bootstrapping server (packages, venv, systemd, nginx)..." -ForegroundColor Green
    # Strip Windows line endings (\r) before running the bash script
    Run-SSH "sed -i 's/\r//' ${INSTALL_DIR}/deploy/server_bootstrap.sh && bash ${INSTALL_DIR}/deploy/server_bootstrap.sh ${INSTALL_DIR}"
}

Write-Host "Restarting service..." -ForegroundColor Green
Run-SSH "systemctl restart oa-pipeline"

# Give gunicorn a moment to bind, then check status
Start-Sleep -Seconds 2
Run-SSH "systemctl status oa-pipeline --no-pager -l"

$ip = (Run-SSH "hostname -I").Trim().Split(' ')[0]
Write-Host ""
Write-Host "=== Deploy complete! ===" -ForegroundColor Cyan
Write-Host "Dashboard: http://${ip}/" -ForegroundColor Cyan

if (-not $RestartOnly) {
    Write-Host ""
    Write-Host "First deploy? Create an admin user:" -ForegroundColor Yellow
    Write-Host "  ssh $Target '/opt/oa-pipeline/venv/bin/python3 /opt/oa-pipeline/dashboard/manage.py create-user admin'"
}
