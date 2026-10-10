# orelhIA Makefile
# https://github.com/evandrodevbr/orelhIA
#
# One-liner: `make dev`
# Single command: `uv sync --extra dev --extra record && uv run python -m parakeet_bootstrap`

.PHONY: help install dev test lint typecheck format clean run cli bootstrap health all

# Default: show help
help:
	@echo "orelhIA — Makefile"
	@echo ""
	@echo "Comandos disponíveis:"
	@echo "  make install    — instala deps (uv sync, requer uv instalado)"
	@echo "  make dev        — install + bootstrap container + sanity test"
	@echo "  make test       — roda pytest"
	@echo "  make lint       — roda ruff check"
	@echo "  make typecheck  — roda mypy"
	@echo "  make format     — roda ruff format"
	@echo "  make clean      — remove cache/build artifacts"
	@echo "  make run        — roda MCP server (stdio, pra usar com pi)"
	@echo "  make cli        — roda CLI standalone (uso direto no terminal)"
	@echo "  make bootstrap  — instala Docker + container Parakeet (idempotente)"
	@echo "  make health     — checa status do backend"
	@echo "  make all        — dev + test + lint"
	@echo ""
	@echo "Variáveis:"
	@echo "  PYTHON=python3.12  uv=0.11.7"

# Install deps via uv (modern Python package manager)
install:
	uv sync --extra dev --extra record

# Full dev setup: install + bootstrap container + smoke test
dev: install bootstrap
	@echo ""
	@echo "✅ Setup completo!"
	@echo "   Para usar via MCP, adicione ao ~/.pi/agent/mcp.json:"
	@echo "   { \"mcpServers\": { \"orelhIA\": { ... } } }"
	@echo ""
	@echo "   Para testar standalone:"
	@echo "   uv run python -m orelhIA.cli audio.ogg -l pt"

# Test
test:
	uv run pytest tests/ -v

# Lint
lint:
	uv run ruff check server.py parakeet_bootstrap.py orelhIA/ tests/

# Type check
typecheck:
	uv run mypy server.py parakeet_bootstrap.py orelhIA/

# Format
format:
	uv run ruff format server.py parakeet_bootstrap.py orelhIA/ tests/

# Clean artifacts
clean:
	rm -rf .pytest_cache .ruff_cache __pycache__ */__pycache__ */*/__pycache__
	rm -rf .venv dist build *.egg-info

# Run MCP server (stdio)
run:
	uv run python -m orelhIA

# Run CLI standalone: make cli AUDIO=audio.ogg [LANG_ISO=pt]
# (não use LANG: é a variável de locale do shell, ex. en_US.UTF-8)
LANG_ISO ?= pt
cli:
	uv run python -m orelhIA.cli $(AUDIO) -l $(LANG_ISO)

# Install Docker + run container (idempotent)
bootstrap:
	uv run python -m parakeet_bootstrap

# Health check on backend
health:
	uv run python -c "import orelhIA; h = orelhIA.health(); print('ok:', h.get('ok')); print('models:', h.get('models', []))"

# Run all checks
all: dev test lint
	@echo ""
	@echo "✅ All checks passed!"
