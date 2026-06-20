# Changelog

All notable changes to **orelhIA** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
