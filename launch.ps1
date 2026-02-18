# ──────────────────────────────────────────────────────────
# Lancelot Launcher — starts Docker and opens the War Room
# ──────────────────────────────────────────────────────────

$WarRoomUrl = "http://localhost:8501"
$HealthUrl = "http://localhost:8000/health/live"
$MaxWait = 120

Write-Host ""
Write-Host "  Starting Lancelot..." -ForegroundColor Cyan
Write-Host ""

# Start containers
docker compose up -d @args

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
Write-Host "  Warning: Health check timed out after ${MaxWait}s." -ForegroundColor Yellow
Write-Host "  Lancelot may still be starting. Check: docker compose logs -f lancelot-core"
Write-Host "  War Room: $WarRoomUrl"
Write-Host ""
