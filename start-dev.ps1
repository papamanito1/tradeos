# TradeOS Quick Start (Windows PowerShell)
# Run: .\start-dev.ps1

Write-Host "TradeOS Dev Setup" -ForegroundColor Cyan

# Check .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env created from .env.example — edit SECRET_KEY before production use" -ForegroundColor Yellow
}

Write-Host "`nStarting Docker services..." -ForegroundColor Cyan
docker compose up --build -d

Write-Host "`nWaiting for services to be ready..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

Write-Host "`nSeeding database..." -ForegroundColor Cyan
docker compose exec backend python -m app.seeds.seed_data

Write-Host "`n✓ TradeOS is ready!" -ForegroundColor Green
Write-Host "  Dashboard:  http://localhost:3000" -ForegroundColor White
Write-Host "  API Docs:   http://localhost:8000/docs" -ForegroundColor White
Write-Host "  Login:      admin / changeme123!" -ForegroundColor White
