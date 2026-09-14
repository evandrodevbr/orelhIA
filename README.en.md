# orelhIA

**MCP server (stdio) that transcribes local audio to text with Parakeet TDT running on your own machine, nothing leaves it.**

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-stdio%20server-6E56CF)
![Backend](https://img.shields.io/badge/backend-Parakeet%20TDT-orange)
![Tests](https://img.shields.io/badge/tests-26%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

[🇧🇷 Português](./README.md) · [🇺🇸 English](./README.en.md) · [Architecture](./docs/ARCHITECTURE.md) · [Contributing](./CONTRIBUTING.md)

## About

Sending audio to a paid API is the usual way to get a transcript. orelhIA takes the local route: it is an MCP server that exposes transcription tools to agents (Claude, pi, IDEs) and talks to a Parakeet TDT backend running in Docker on the same machine. The agent calls `transcribe_file("/tmp/audio.ogg")` and gets the text back, with an on-disk cache to avoid re-transcribing the same audio, optional VAD to trim silence, usage metrics and direct microphone recording.

## How it works

```
MCP agent (Claude, pi, IDE)
        │  JSON-RPC over stdio
        ▼
orelhIA (server.py, FastMCP, 7 tools)
        │  multipart POST to /v1/audio/transcriptions (OpenAI-compatible API)
        ▼
local transcription backend (Parakeet TDT in Docker)
  parakeet-ptbr :8022  (CPU)   or   parakeet-gpu :5092  (CUDA)
        ▲
        └── on-disk LRU cache (~/.orelhIA/cache, key = SHA-256 of audio + params)
```

- The server speaks MCP over stdio: no HTTP port of its own, the MCP client spawns the process.
- `transcribe_file` validates (path, format, size), checks the LRU cache by content hash, applies energy-based VAD when requested and POSTs multipart to the backend, retrying on 5xx.
- `transcribe_url` downloads public http(s) URLs behind an SSRF guard (each redirect hop is revalidated).
- `record_audio` records from the microphone through PyAudio, writes a 16-bit PCM WAV and transcribes it.
- `bootstrap_parakeet` (and `python -m parakeet_bootstrap`) handles the Docker lifecycle idempotently: Docker present, daemon running, image present, container running, `/health` healthy.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.10+ (tested on 3.13) |
| Protocol | MCP over stdio, `mcp[cli]` SDK `>=1.0,<2` (FastMCP) |
| Transcription backend | Parakeet TDT 0.6B v3 (ONNX) in a Docker container, served over an OpenAI-compatible `/v1/audio/transcriptions` API; Whisper-compatible backends (e.g. Speaches) also work |
| Cache | On-disk LRU (`~/.orelhIA/cache/index.json`), SHA-256 key |
| VAD | Energy-based, stdlib only (`struct` + `wave`), no extra dependency |
| Recording | PyAudio (`record` extra) |
| Tests | pytest + `unittest.mock` (no network, no Docker) |
| Quality | ruff (lint/format) and mypy, configured in `pyproject.toml` |
| Packages | uv (`uv sync`, `uv build`); `pip install -e .` works too |
| Bootstrap | `parakeet_bootstrap.py` shelling out to the Docker CLI |

## Requirements

- Python `>=3.10`
- Docker (Engine or Desktop) with the daemon running, for the transcription backend
- A backend image available on the machine (the repository ships no Dockerfile; the default name is `parakeet-tdt:ptbr-cpu`, configurable)
- Disk space for the backend image (the CPU image used in testing is ~10 GB)
- Optional: PyAudio and a microphone, only for `record_audio` (`record` extra)

## Quick start

Commands below were executed and verified in this repository (Linux, Python 3.13, uv):

```bash
git clone https://github.com/evandrodevbr/orelhIA.git
cd orelhIA

# installs the project (editable) + dev and recording extras into .venv
uv sync --extra dev --extra record
# pip alternative:
# pip install -e ".[dev,record]"

# idempotent backend bring-up (Docker + image + container + health)
uv run python -m parakeet_bootstrap
# if your image/container/port differ:
# uv run python -m parakeet_bootstrap --image parakeet-tdt:cpu --container parakeet-cpu --port 5092

# sanity check
uv run python -m orelhIA.cli --health

# transcribe
uv run python -m orelhIA.cli audio.ogg -l pt
```

Register the MCP server in your client (config tested with an `initialize` and `tools/list` handshake):

```json
{
  "mcpServers": {
    "orelhIA": {
      "command": "uv",
      "args": ["--directory", "/path/to/orelhIA", "run", "python", "-m", "orelhIA"]
    }
  }
}
```

`Makefile` shortcuts (tested): `make test`, `make lint`, `make health`, `make run` (MCP over stdio), `make bootstrap`. `Taskfile.yml` is the cross-platform alternative (YAML validated; the `task` runner was not installed in the verification environment).

## Usage

### MCP tools

| Tool | What it does | Parameters |
|---|---|---|
| `health()` | Backend status + features (tries `/v1/models`, falls back to `/health`) | none |
| `transcribe_file(path, language?, model?, response_format?, preprocess?)` | Local file to text | `path` required; `response_format`: `json`/`text`/`srt`/`vtt`; `preprocess`: `none`/`vad` |
| `transcribe_url(url, language?, model?)` | Downloads a public http(s) URL and transcribes it | `url` required |
| `record_audio(seconds, output_path?, language?, model?, sample_rate?)` | Records from the microphone and transcribes | `seconds` from 1 to 600 |
| `get_metrics()` | Counters, latency, cache hit rate, bytes processed | none |
| `clear_cache()` | Clears the LRU cache and returns how many entries were removed | none |
| `bootstrap_parakeet(port?, image?, container?)` | Docker + image + container, idempotent | all optional |

The server identifies itself as `whisper` in the MCP handshake (name inherited from earlier versions).

### CLI

```bash
python -m orelhIA.cli audio.ogg -l pt              # text
python -m orelhIA.cli audio.wav --format json      # full result
python -m orelhIA.cli audio.wav --preprocess vad   # trim silence first
python -m orelhIA.cli https://host/audio.ogg --url -l pt
python -m orelhIA.cli --record 5 --save speech.wav # microphone for 5s
python -m orelhIA.cli --health | --metrics | --clear-cache
```

| Flag | Effect |
|---|---|
| `-l, --language` | ISO-639-1 code (`pt`, `en`, ...) |
| `-m, --model` | Backend model override |
| `--url` | Treat the argument as a URL |
| `--format` | `text` (default), `json`, `srt`, `vtt` |
| `--preprocess` | `none` (default) or `vad` |
| `--record N` / `--save PATH` | Record N seconds from the microphone / save the WAV |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `ORELHIA_BASE_URL` | `http://localhost:5092` | Backend URL |
| `ORELHIA_MODEL` | `istupakov/parakeet-tdt-0.6b-v3-onnx` | Default model sent in the POST |
| `ORELHIA_TIMEOUT` | `120` | HTTP timeout (seconds) |
| `ORELHIA_MAX_BYTES` | `26214400` (25 MB) | Audio size limit |
| `ORELHIA_ALLOW_PRIVATE_URLS` | `false` | Allows private URLs in `transcribe_url` (dev only) |
| `ORELHIA_CACHE_DIR` | `~/.orelhIA/cache` | LRU cache directory |
| `ORELHIA_CACHE_MAX_ENTRIES` | `128` | Max cache entries |
| `ORELHIA_VAD_RMS_THRESHOLD` | `0.01` | VAD RMS threshold (0 to 1 scale) |
| `ORELHIA_VAD_MIN_SPEECH_MS` | `250` | Minimum continuous speech to keep |
| `ORELHIA_VAD_PAD_MS` | `100` | Padding before/after speech chunks |
| `ORELHIA_RECORD_SAMPLE_RATE` | `16000` | Recording sample rate |
| `ORELHIA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Tool error codes

Errors come back as structured results (`{"error": ..., "code": ...}`) without crashing the server: `file_not_found`, `unsupported_format`, `file_too_large`, `invalid_scheme`, `private_url_blocked`, `download_failed`, `connection_error`, `http_error`, `retry_exhausted`, `pyaudio_unavailable`, `invalid_duration`, `recording_failed`, `write_failed`, `unexpected_error`.

## Production

This is a local stdio server: there is no HTTP endpoint to deploy. What exists to package and run:

```bash
uv build                          # produces dist/orelhia-3.0.0-py3-none-any.whl (orelhIA/, server.py, parakeet_bootstrap.py) and the sdist
pip install dist/orelhia-*.whl    # the installed package imports from any directory
python -m orelhIA                 # starts the MCP server over stdio (the MCP client spawns it)
```

The backend process is managed by the idempotent bootstrap (`bootstrap_parakeet()` or `python -m parakeet_bootstrap`), with `--restart unless-stopped` on the container.

## Project layout

```
server.py                    MCP server (FastMCP) + the 7 tools, cache, VAD, metrics, SSRF guard
parakeet_bootstrap.py        Docker lifecycle (image, container, /health), idempotent
orelhIA/
├── __init__.py              re-exports from server.py (compatibility)
├── __main__.py              stdio entrypoint: python -m orelhIA
└── cli/__main__.py          standalone CLI: python -m orelhIA.cli
tests/
└── test_parakeet_bootstrap.py  26 unit tests for the bootstrap (mocks, no Docker)
docs/ARCHITECTURE.md         diagrams and architecture decisions
Makefile / Taskfile.yml      dev/test/lint/run shortcuts
install.sh / install.ps1     installers (uv + deps + bootstrap)
```

## Tests and verification

```bash
uv run pytest                                    # 26 passed
uv run pytest tests/test_parakeet_bootstrap.py   # bootstrap only
uv run ruff check server.py parakeet_bootstrap.py orelhIA/ tests/   # clean
uv run mypy server.py parakeet_bootstrap.py                          # clean
uv run python -m orelhIA.cli --health                                # real backend API
```

What the tests actually cover: 26 unit cases for `parakeet_bootstrap` (platform/distro detection, Docker, image, container, idempotency, progress callbacks) using `unittest.mock`, no network and no Docker. There are no automated tests for `server.py` and no CI. The rest of the verification is manual: bring the backend up, run `--health`, transcribe a file and check `get_metrics`.

## Current state and limitations

- The repository ships no Dockerfile or build recipe for the backend image. `parakeet_bootstrap` assumes the image already exists on the machine; a `docker pull` of the default name (`parakeet-tdt:ptbr-cpu`) fails because no public registry serves it. Use `--image`/`--container`/`--port` (or the `bootstrap_parakeet` parameters) for the image you have.
- No GitHub CI (no workflows in the repository).
- It transcribes whole files; there is no streaming or incremental transcription.
- `preprocess="vad"` only acts on 16-bit PCM WAV; for other formats the VAD is skipped with a log line.
- `record_audio` depends on PyAudio (`record` extra) and an input device; with no speech the text comes back empty.
- `transcribe_url` accepts http(s) only; private networks are blocked by default (SSRF guard).
- Metrics are in memory and reset on restart; `get_metrics` and `clear_cache` are not counted.
- `uv.lock` is not committed (`uv sync` resolves on the fly); the only version ceiling today is `mcp>=1.0,<2`.
- macOS is not supported by the bootstrap (Windows and Linux are, by design).
- No authentication on the backend: the container is exposed on loopback.

## Documentation

| Document | Content |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Diagrams (topology, layers, flows), security model, design decisions |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Development setup, style, PR process |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |

## License

MIT, see [`LICENSE`](LICENSE). Credits: Parakeet TDT 0.6B v3 (NVIDIA NeMo), pt-BR TAGARELA fine-tune ([Alefiury](https://huggingface.co/alefiury)), [Speaches](https://github.com/speaches-ai/speaches), [MCP](https://modelcontextprotocol.io).
