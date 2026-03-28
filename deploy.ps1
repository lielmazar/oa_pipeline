<#
.SYNOPSIS
    Deploy OpenAlex Dashboard and pipeline scripts to the Ubuntu server.

.DESCRIPTION
    Copies source code and optionally database files to the remote server
    via scp, then restarts the dashboard service.

.PARAMETER Server
    Server IP address or hostname (required). Can also be set via
    the OPENALEX_SERVER environment variable.

.PARAMETER User
    SSH username (default: root).

.PARAMETER RemoteBase
    Base directory on the server (default: /root/data/openalex).

.PARAMETER IncludeScripts
    Also copy the pipeline Python scripts (process_all_works.py etc.)

.PARAMETER IncludeSubdir2
    Also copy the 2/ subdirectory scripts (postprocess_ml_check.py etc.)

.PARAMETER CopyDb
    Copy a local SQLite database to the server.

.PARAMETER DbFile
    Which local database file to copy (default: openalex_works_filtered.sqlite).
    Can also be a path relative to the local server/ directory.

.PARAMETER NoRestart
    Skip restarting the systemd service after deploy.

.EXAMPLE
    # Deploy dashboard code only
    .\deploy.ps1 -Server 192.168.1.100

    # Deploy dashboard + pipeline scripts
    .\deploy.ps1 -Server 192.168.1.100 -IncludeScripts

    # Deploy everything + copy local DB
    .\deploy.ps1 -Server 192.168.1.100 -IncludeScripts -IncludeSubdir2 -CopyDb

    # Copy a specific DB file
    .\deploy.ps1 -Server 192.168.1.100 -CopyDb -DbFile "0\openalex_works_filtered.sqlite"

    # Set server once via env var, then just run
    $env:OPENALEX_SERVER = "192.168.1.100"
    .\deploy.ps1
#>

param(
    [string]$Server     = $env:OPENALEX_SERVER,
    [string]$User       = "root",
    [string]$RemoteBase = "/root/data/openalex",
    [switch]$IncludeScripts,
    [switch]$IncludeSubdir2,
    [switch]$CopyDb,
    [string]$DbFile     = "openalex_works_filtered.sqlite",
    [switch]$NoRestart
)

# ── Validate ──────────────────────────────────────────────────────────────────

if (-not $Server) {
    Write-Error "Server is required. Pass -Server <IP> or set `$env:OPENALEX_SERVER."
    exit 1
}

$LocalBase = Split-Path -Parent $MyInvocation.MyCommand.Path
$Remote    = "${User}@${Server}"

Write-Host ""
Write-Host "OpenAlex Deploy" -ForegroundColor Cyan
Write-Host "  Server : $Remote" -ForegroundColor DarkGray
Write-Host "  Remote : $RemoteBase" -ForegroundColor DarkGray
Write-Host "  Local  : $LocalBase" -ForegroundColor DarkGray
Write-Host ""

$errors = 0

# ── Helpers ───────────────────────────────────────────────────────────────────

function Step([string]$msg) {
    Write-Host "▸ $msg" -ForegroundColor Yellow
}

function OK([string]$msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}

function Fail([string]$msg) {
    Write-Host "  ✗ $msg" -ForegroundColor Red
    $script:errors++
}

function Run-SCP([string]$src, [string]$dest, [switch]$Recurse) {
    $flag = if ($Recurse) { "-r" } else { "" }
    Write-Host "  scp $src → ${Remote}:$dest" -ForegroundColor DarkGray
    if ($Recurse) {
        scp -r "$src" "${Remote}:${dest}"
    } else {
        scp "$src" "${Remote}:${dest}"
    }
    if ($LASTEXITCODE -ne 0) { Fail "scp failed for $src" }
    else { OK (Split-Path -Leaf $src) }
}

function Run-SSH([string]$cmd) {
    Write-Host "  ssh: $cmd" -ForegroundColor DarkGray
    ssh "${Remote}" "$cmd"
    if ($LASTEXITCODE -ne 0) { Fail "SSH command failed: $cmd" }
}

# ── Ensure remote directories exist ──────────────────────────────────────────

Step "Ensuring remote directories exist"
Run-SSH "mkdir -p ${RemoteBase}/dashboard/templates ${RemoteBase}/dashboard/static ${RemoteBase}/batch_runs ${RemoteBase}/2"

# ── Dashboard source ──────────────────────────────────────────────────────────

Step "Copying dashboard source"

$dashFiles = @(
    "dashboard\app.py",
    "dashboard\config.py",
    "dashboard\job_manager.py",
    "dashboard\db_api.py",
    "dashboard\requirements.txt",
    "dashboard\start.sh"
)
foreach ($f in $dashFiles) {
    $local = Join-Path $LocalBase $f
    if (Test-Path $local) {
        Run-SCP $local "${RemoteBase}/dashboard/"
    } else {
        Fail "Not found: $f"
    }
}

Run-SCP (Join-Path $LocalBase "dashboard\templates\index.html") "${RemoteBase}/dashboard/templates/"
Run-SCP (Join-Path $LocalBase "dashboard\static\app.js")        "${RemoteBase}/dashboard/static/"

# Make start.sh executable
Run-SSH "chmod +x ${RemoteBase}/dashboard/start.sh"

# ── Pipeline scripts ──────────────────────────────────────────────────────────

if ($IncludeScripts -or $IncludeSubdir2) {
    Step "Copying pipeline scripts"
}

if ($IncludeScripts) {
    $scripts = @(
        "Proccess_dataset.py",
        "process_all_works.py",
        "generate_report.py",
        "batch_process_papers.py"
    )
    foreach ($s in $scripts) {
        $local = Join-Path $LocalBase $s
        if (Test-Path $local) {
            Run-SCP $local "${RemoteBase}/"
        } else {
            Write-Host "  (skipped, not found locally: $s)" -ForegroundColor DarkGray
        }
    }
}

if ($IncludeSubdir2) {
    $scripts2 = @(
        "2\postprocess_ml_check.py",
        "2\generate_report.py",
        "2\process_all_works.py",
        "2\Proccess_dataset.py"
    )
    foreach ($s in $scripts2) {
        $local = Join-Path $LocalBase $s
        if (Test-Path $local) {
            Run-SCP $local "${RemoteBase}/2/"
        } else {
            Write-Host "  (skipped, not found locally: $s)" -ForegroundColor DarkGray
        }
    }
}

# ── Database ──────────────────────────────────────────────────────────────────

if ($CopyDb) {
    Step "Copying database: $DbFile"
    $localDb = Join-Path $LocalBase $DbFile
    if (-not (Test-Path $localDb)) {
        Fail "Database file not found: $localDb"
    } else {
        $sizeMb = [math]::Round((Get-Item $localDb).Length / 1MB, 1)
        Write-Host "  Size: ${sizeMb} MB" -ForegroundColor DarkGray

        # Determine remote destination (preserve subdir if present)
        $dbParent = Split-Path -Parent $DbFile
        if ($dbParent -and $dbParent -ne ".") {
            $remoteDest = "${RemoteBase}/${dbParent}/"
            Run-SSH "mkdir -p ${remoteDest}"
        } else {
            $remoteDest = "${RemoteBase}/"
        }
        Run-SCP $localDb $remoteDest
    }
}

# ── Restart service ───────────────────────────────────────────────────────────

if (-not $NoRestart) {
    Step "Restarting dashboard service"
    $svcStatus = ssh "${Remote}" "systemctl is-active openalex-dashboard 2>/dev/null"
    if ($svcStatus -eq "active") {
        Run-SSH "systemctl restart openalex-dashboard"
        Start-Sleep -Seconds 2
        $newStatus = ssh "${Remote}" "systemctl is-active openalex-dashboard"
        if ($newStatus -eq "active") { OK "Service restarted successfully" }
        else { Fail "Service failed to restart — check: journalctl -u openalex-dashboard -n 30" }
    } else {
        Write-Host "  (service not running — skipping restart)" -ForegroundColor DarkGray
        Write-Host "  To start:  ssh ${Remote} systemctl start openalex-dashboard" -ForegroundColor DarkGray
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
if ($errors -eq 0) {
    Write-Host "Deploy complete " -ForegroundColor Green -NoNewline
    Write-Host "(0 errors)" -ForegroundColor DarkGray
    Write-Host "  Dashboard: http://$Server/" -ForegroundColor Cyan
} else {
    Write-Host "Deploy finished with $errors error(s)" -ForegroundColor Red
    exit 1
}
