# ──────────────────────────────────────────────────────────
# Lancelot Launcher — starts Docker and opens the War Room
# ──────────────────────────────────────────────────────────

$WarRoomUrl = "http://localhost:8501"
$HealthUrl = "http://localhost:8000/health/live"
$MaxWait = 120
$IssuesUrl = "https://github.com/myles1663/lancelot/issues"

function Show-FatalError {
    param([string]$Message, [string]$Fix)
    Write-Host ""
    Write-Host "  ERROR: $Message" -ForegroundColor Red
    if ($Fix) {
        Write-Host "  Fix:   $Fix" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  If this doesn't resolve the issue, open a ticket:" -ForegroundColor Gray
    Write-Host "  $IssuesUrl" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# ── Pre-flight checks ──────────────────────────────────────

Write-Host ""
Write-Host "  Lancelot — Pre-flight checks" -ForegroundColor Cyan
Write-Host ""

# 1. Docker CLI
try {
    $null = Get-Command docker -ErrorAction Stop
    Write-Host "  [OK] Docker CLI found" -ForegroundColor Green
} catch {
    Show-FatalError "Docker is not installed." "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
}

# 2. Docker daemon running
try {
    $dockerInfo = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw "not running" }
    Write-Host "  [OK] Docker daemon running" -ForegroundColor Green
} catch {
    Show-FatalError "Docker is not running." "Start Docker Desktop and try again."
}

# 3. Port 8000 available
$port8000 = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($port8000) {
    $proc = Get-Process -Id $port8000[0].OwningProcess -ErrorAction SilentlyContinue
    $procName = if ($proc) { $proc.ProcessName } else { "unknown" }
    Show-FatalError "Port 8000 is already in use (by $procName)." "Stop the process using port 8000, or run: Stop-Process -Id $($port8000[0].OwningProcess)"
}
Write-Host "  [OK] Port 8000 available" -ForegroundColor Green

# 4. Port 8080 available
$port8080 = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($port8080) {
    $proc = Get-Process -Id $port8080[0].OwningProcess -ErrorAction SilentlyContinue
    $procName = if ($proc) { $proc.ProcessName } else { "unknown" }
    Show-FatalError "Port 8080 is already in use (by $procName)." "Stop the process using port 8080, or run: Stop-Process -Id $($port8080[0].OwningProcess)"
}
Write-Host "  [OK] Port 8080 available" -ForegroundColor Green

Write-Host ""

# ── Start containers ────────────────────────────────────────

Write-Host "  Starting Lancelot..." -ForegroundColor Cyan
Write-Host ""

docker compose up -d @args
if ($LASTEXITCODE -ne 0) {
    Show-FatalError "docker compose up failed (exit code $LASTEXITCODE)." "Check output above. Run 'docker compose logs' for details."
}

Write-Host ""
Write-Host "  Waiting for Lancelot to become healthy..." -ForegroundColor Gray

$elapsed = 0
while ($elapsed -lt $MaxWait) {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host ""
            Write-Host "  Lancelot is ready!" -ForegroundColor Green
            Write-Host ""
            Write-Host "  War Room: $WarRoomUrl" -ForegroundColor White
            Write-Host "  API:      http://localhost:8000" -ForegroundColor White
            Write-Host ""

            Start-Process $WarRoomUrl
            exit 0
        }
    } catch {
        # Not ready yet
    }
    Start-Sleep -Seconds 2
    $elapsed += 2
    Write-Host "`r  Waiting... ${elapsed}s / ${MaxWait}s" -NoNewline
}

Write-Host ""
Write-Host ""
Write-Host "  WARNING: Health check timed out after ${MaxWait}s." -ForegroundColor Yellow
Write-Host "  Lancelot may still be starting. Check: docker compose logs -f lancelot-core" -ForegroundColor Yellow
Write-Host "  War Room: $WarRoomUrl"
Write-Host ""
Write-Host "  If this doesn't resolve the issue, open a ticket:" -ForegroundColor Gray
Write-Host "  $IssuesUrl" -ForegroundColor Cyan
Write-Host ""
