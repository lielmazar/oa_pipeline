<#
.SYNOPSIS
    Deploy OA Pipeline to Ubuntu server from PowerShell.

.DESCRIPTION
    Builds the React frontend, syncs code, and bootstraps the server.
    Safe to run for both first-time setup and routine updates.
    Requires SSH key auth -- see README.

.PARAMETER Target
    SSH target in user@host format, e.g. root@157.180.11.56

.PARAMETER RestartOnly
    Skip file sync and build; just restart the systemd service.

.PARAMETER SkipBuild
    Skip the React npm build step.

.EXAMPLE
    .\deploy\deploy.ps1 root@157.180.11.56
    .\deploy\deploy.ps1 root@157.180.11.56 -RestartOnly
    .\deploy\deploy.ps1 root@157.180.11.56 -SkipBuild
#>
param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Target,
    [switch]$RestartOnly,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$INSTALL_DIR = "/opt/oa-pipeline"
$REPO_ROOT = Split-Path -Parent $PSScriptRoot
$SSH_OPTS = @("-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3")

function Run-SSH {
    param([string]$Cmd)
    ssh @SSH_OPTS $Target $Cmd
}

function Run-SCP-R {
    param([string]$Src, [string]$Dest)
    scp @SSH_OPTS -r $Src "${Target}:${Dest}"
}

function Run-SCP-F {
    param([string]$Src, [string]$Dest)
    scp @SSH_OPTS $Src "${Target}:${Dest}"
}

Write-Host "=== Deploying to $Target ===" -ForegroundColor Cyan

if ($RestartOnly) {
    Write-Host "Restarting service..." -ForegroundColor Green
    Run-SSH "systemctl restart oa-pipeline && systemctl status oa-pipeline --no-pager -l"
    Write-Host "=== Done ===" -ForegroundColor Cyan
    exit 0
}

# Step 1: Build React frontend
if (-not $SkipBuild) {
    Write-Host "[1/5] Building React frontend..." -ForegroundColor Green
    $WebDir = Join-Path $REPO_ROOT "web"
    $NpmCmd = $null
    $NpmCandidates = @(
        "C:\Program Files\nodejs\npm.cmd",
        "$env:APPDATA\npm\npm.cmd",
        "$env:LOCALAPPDATA\Programs\nodejs\npm.cmd"
    )
    foreach ($p in $NpmCandidates) {
        if (Test-Path $p) {
            $NpmCmd = $p
            break
        }
    }
    if ($null -eq $NpmCmd) {
        try {
            $null = Get-Command "npm" -ErrorAction Stop
            $NpmCmd = "npm"
        } catch {}
    }
    # Vite/npm child processes must find node.exe — on Windows, PATH often lacks Node when npm is invoked by full path.
    $nodeDirs = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($d in @(
        "C:\Program Files\nodejs",
        "${env:ProgramFiles}\nodejs",
        "${env:LOCALAPPDATA}\Programs\nodejs"
    )) { if (Test-Path (Join-Path $d "node.exe")) { [void]$nodeDirs.Add($d) } }
    if ($NvmHome = $env:NVM_HOME) {
        $nvmNode = Join-Path $NvmHome "nvm"
        if (Test-Path $nvmNode) { [void]$nodeDirs.Add($nvmNode) }
    }
    if ($NpmCmd -match '[\\/]') {
        $npmParent = Split-Path -Parent $NpmCmd
        if (Test-Path (Join-Path $npmParent "node.exe")) { [void]$nodeDirs.Add($npmParent) }
    } else {
        $npmWhich = Get-Command "npm" -ErrorAction SilentlyContinue
        if ($npmWhich -and $npmWhich.Source) {
            $npmParent = Split-Path -Parent $npmWhich.Source
            if (Test-Path (Join-Path $npmParent "node.exe")) { [void]$nodeDirs.Add($npmParent) }
        }
    }
    foreach ($d in $nodeDirs) {
        if ($env:PATH -notlike "*$d*") { $env:PATH = "$d;$env:PATH" }
    }
    if ($null -ne $NpmCmd) {
        Push-Location $WebDir
        & $NpmCmd ci --silent
        if (-not $?) { throw "npm ci failed" }
        & $NpmCmd run build
        if (-not $?) { throw "npm run build failed (is node.exe on PATH?)" }
        Pop-Location
        Write-Host "    -> built to api/static/" -ForegroundColor DarkGray
    } else {
        Write-Host "    [warn] npm not found, skipping build. Run with -SkipBuild to suppress." -ForegroundColor Yellow
    }
}

# Step 2: Prepare remote dirs
Write-Host "[2/5] Preparing remote directories..." -ForegroundColor Green
Run-SSH "mkdir -p ${INSTALL_DIR}/data/pipelines"

# Step 3: Sync code
Write-Host "[3/5] Syncing Python backend (api/ + pipeline/)..." -ForegroundColor Green
Run-SSH "rm -rf ${INSTALL_DIR}/api"
Run-SCP-R "$REPO_ROOT\api" "${INSTALL_DIR}/"
Run-SSH "rm -rf ${INSTALL_DIR}/pipeline"
Run-SCP-R "$REPO_ROOT\pipeline" "${INSTALL_DIR}/"

# Step 4: Sync config + deploy scripts
Write-Host "[4/5] Syncing config + deploy scripts..." -ForegroundColor Green
Run-SCP-F "$REPO_ROOT\pyproject.toml" "${INSTALL_DIR}/pyproject.toml"
Run-SSH "rm -rf ${INSTALL_DIR}/deploy"
Run-SCP-R "$REPO_ROOT\deploy" "${INSTALL_DIR}/"

$EnvFile = Join-Path $REPO_ROOT ".env"
if (Test-Path $EnvFile) {
    Write-Host "    Copying .env to server..." -ForegroundColor DarkGray
    Run-SCP-F $EnvFile "${INSTALL_DIR}/data/.env"
    Run-SSH "chmod 600 ${INSTALL_DIR}/data/.env"
}

# Step 5: Bootstrap server
Write-Host "[5/5] Bootstrapping server (packages, venv, systemd, nginx)..." -ForegroundColor Green
Run-SSH "sed -i 's/\r//' ${INSTALL_DIR}/deploy/server_bootstrap.sh && bash ${INSTALL_DIR}/deploy/server_bootstrap.sh ${INSTALL_DIR}"

# Restart + status
Write-Host "Restarting service..." -ForegroundColor Green
Run-SSH "systemctl restart oa-pipeline"
Start-Sleep -Seconds 2
Run-SSH "systemctl status oa-pipeline --no-pager -l"

$ip = (Run-SSH "hostname -I").Trim().Split(' ')[0]
Write-Host ""
Write-Host "=== Deploy complete! ===" -ForegroundColor Cyan
Write-Host "App: http://${ip}/" -ForegroundColor Cyan
