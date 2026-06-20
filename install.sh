#!/usr/bin/env bash
# orelhIA one-liner installer
# Usage: ./install.sh
# Or remotely: curl -fsSL https://raw.githubusercontent.com/evandrodevbr/orelhIA/main/install.sh | bash
#
# Requisitos: uv (https://docs.astral.sh/uv/) e Docker Desktop
# Funciona em Linux e macOS (WIP). Para Windows, use install.ps1 ou WSL.

set -euo pipefail

# 1. Install uv se faltar
if ! command -v uv &> /dev/null; then
  echo "📦 Instalando uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install Docker se faltar (Linux only)
if [[ "$(uname)" == "Linux" ]] && ! command -v docker &> /dev/null; then
  echo "🐳 Instalando Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER || true
fi

# 3. Sync deps
echo "🔄 Sincronizando dependências..."
uv sync --extra dev --extra record

# 4. Bootstrap container
echo "🚀 Iniciando Parakeet container..."
uv run python -m parakeet_bootstrap

# 5. Health check
echo "🏥 Health check..."
uv run python -c "import orelhIA; h = orelhIA.health(); print('OK' if h.get('ok') else 'FAIL', h)"

echo ""
echo "✅ orelhIA instalado!"
echo "   Para usar via MCP, adicione ao ~/.pi/agent/mcp.json:"
echo '   { "mcpServers": { "orelhIA": { "command": "uv", "args": ["--directory", "'$(pwd)'", "run", "python", "-m", "orelhIA"] } } }'
