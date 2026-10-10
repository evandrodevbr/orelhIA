# Changelog

All notable changes to **orelhIA** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `record_audio` ignorava `ORELHIA_RECORD_SAMPLE_RATE` (16000 fixo); `transcribe_url` não contava `file_too_large` como erro nas métricas; `output_path` inválido em `record_audio` levantava exceção em vez de retornar `write_failed`.
- `orelhIA/cli` não tinha `__init__.py` e ficava fora do wheel; a CLI inseria o diretório errado no `sys.path`.
- `make cli`/`task cli` usavam `$(LANG)` (locale do shell, ex. `en_US.UTF-8`) como código de idioma; agora `LANG_ISO` (padrão `pt`).
- **`mcp` 2.x quebrava o servidor**: `mcp[cli]` sem teto de versão resolvia para a 2.x, que renomeou `FastMCP` para `MCPServer`. Dependência fixada em `mcp>=1.0,<2`.
- **`python -m orelhIA` não rodava**: não existia `orelhIA/__main__.py`; adicionado o entrypoint stdio que o README, Makefile e scripts de install referenciam.
- **Pacote instalado não importava fora do diretório do repo**: `orelhIA/__init__.py` reexporta `server.py` (módulo da raiz do repo), que não era instalado. Adicionados `[build-system]` e `[tool.setuptools] py-modules = ["server", "parakeet_bootstrap"]`.
- **`NameError` no bootstrap Linux**: `parakeet_bootstrap.py` usava `Path` sem importar `pathlib`.
- **`Taskfile.yml` era YAML inválido**: chave `dev:install` sem valor e `desc` com `: ` sem aspas.
- **Métricas contavam requests em dobro** em cache hit e em erros (o `record_request` era chamado no início e de novo no fim do tool); agora cada chamada é contada uma vez, no desfecho.
- **`record_audio` gravava o WAV duas vezes** quando `output_path` era informado.
- E-mail placeholder `evandro@example.com` trocado pelo endereço real em `pyproject.toml` e `CONTRIBUTING.md`.

### Changed
- `transcribe_url` lê no máximo `MAX_BYTES + 1` bytes em vez de carregar o download inteiro antes de checar o limite.
- Refatoração sem mudança de comportamento: `_is_private_host`, `_vad_trim_wav` e `transcribe_file` divididos em helpers (CC 20/27/20 para no máximo 10), com testes dos caminhos-base em `tests/test_server_paths.py`.
- Workflow de CI (ruff, mypy, pytest) e alvos `typecheck` no Makefile/Taskfile; removido `LRUCache.hash_bytes` (sem uso).
- `ruff check` e `mypy` limpos (ordenação de imports, tipagem PEP 604, `__all__` ordenado, per-file-ignores para `N999`/`E402`).
- `tests/test_parakeet_bootstrap.py`: `test_bootstrap_rejects_unknown_os` agora também mocka `_linux_distro` (falhava em qualquer Linux com Docker instalado).
- README reescrito (pt-BR e inglês) com comandos verificados, tabelas de tools/CLI/env vars e limitações reais.

## [3.0.0] — 2026-06-20

### Added
- **`bootstrap_parakeet()` MCP tool** — install Docker + run container, idempotent
- **`parakeet_bootstrap.py`** standalone CLI: `python -m parakeet_bootstrap`
- **26 unit tests** for `parakeet_bootstrap` (pytest, 100% pass)
- **LRU cache** for transcriptions (SHA-256 content hash, 128 entries)
- **Energy-based VAD** preprocessing (`preprocess="vad"`)
- **In-memory metrics** (counters, latency, cache hit rate)
- **SSRF guard** with redirect revalidation (private IP blocks)
- **MIME map** for `.ogg`/`.opus`/`webm`
- **Retry with backoff** on 5xx backend errors
- **Structured error codes** (file_not_found, unsupported_format, file_too_large, etc.)
- **`record_audio()` tool** for microphone capture (PyAudio, WAV PCM 16-bit)
- **CLI** (`cli/whisper`) for standalone usage outside MCP
- **Parakeet TDT** backend support (NeMo, ONNX, GPU via CUDA + CPU)
- **Multi-model** support: `parakeet-tdt-0.6b-v3`, `istupakov`, `grikdotnet/fp16`, `alefiury/ptBR-TAGARELA`

### Changed
- **Backend default**: `parakeet-tdt:ptbr-cpu` container on `localhost:8022` (fallback)
  or `parakeet-tdt:gpu` on `localhost:5092` (CUDA accelerated)
- **Health check** tries `/v1/models` (Whisper) then falls back to `/health` (Parakeet)
- **Default model**: `alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx` (PT-BR native)

### Security
- `HTTPRedirectHandler` subclass revalidates each hop against private IP list
- `mkstemp` FD leak fixed (now `os.close(fd)` after path extraction)
- Dead `out.write_bytes(raw PCM)` removed (corrupted user file on partial failure)
- `curl | sh` pipe removed — script downloaded to temp, size-capped, then executed
- Max audio file size: 25 MB (configurable via `ORELHIA_MAX_BYTES`)

### Fixed
- Tab transition black flash
- Battery warning truncated text
- UTF-8 byte corruption in PT-BR strings
- Health check 404 on Parakeet (no `/v1/models` endpoint)

## [2.0.0] — 2026-06-15

### Added
- OpenAI-compatible `/v1/audio/transcriptions` API
- Whisper large-v3 turbo model (CTranslate2)
- URL transcription (`transcribe_url`)
- Basic validation (SSRF, size limit)

## [1.0.0] — 2026-06-10

### Added
- Initial MCP server with stdio transport
- Local file transcription (`transcribe_file`)
- Speaches backend integration
