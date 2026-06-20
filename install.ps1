# orelhIA Windows installer
# Usage: .\install.ps1
# Requisitos: PowerShell 5+ e Docker Desktop
# Para Linux/macOS use ./install.sh

$ErrorActionPreference = "Stop"

# 1. Install uv se faltar
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv..." -ForegroundColor Cyan
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}

# 2. Check Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERRO: Docker nao encontrado." -ForegroundColor Red
    Write-Host "Instale Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
}

# 3. Sync deps
Write-Host "Sincronizando dependencias..." -ForegroundColor Cyan
uv sync --extra dev --extra record

# 4. Bootstrap container
Write-Host "Iniciando Parakeet container..." -ForegroundColor Cyan
uv run python -m parakeet_bootstrap

# 5. Health check
Write-Host "Health check..." -ForegroundColor Cyan
uv run python -c "import orelhIA; h = orelhIA.health(); print('OK' if h.get('ok') else 'FAIL', h)"

Write-Host ""
Write-Host "orelhIA instalado!" -ForegroundColor Green
Write-Host "Para usar via MCP, adicione ao C:\Users\Evandro\.pi\agent\mcp.json:"
Write-Host '  { "mcpServers": { "orelhIA": { "command": "uv", "args": ["--directory", "' + (Get-Location).Path + '", "run", "python", "-m", "orelhIA"] } } }'
