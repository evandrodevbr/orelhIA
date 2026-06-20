# Architecture

> Design rationale, system topology, and data flow for **orelhIA v3.0**.

## System overview

```mermaid
flowchart LR
  Client["MCP Client<br/>(pi, Claude, IDE)"]
  subgraph MCP["MCP Server (stdio)"]
    Server["server.py<br/>FastMCP"]
    Boot["parakeet_bootstrap.py<br/>Docker lifecycle"]
    Cache[("LRU Cache<br/>~/.orelhIA/cache")]
    Metrics[/"Metrics<br/>in-memory"/]
  end
  subgraph Backend["Transcription Backend"]
    CPU["parakeet-ptbr<br/>CPU :8022<br/>alefiury/TAGARELA"]
    GPU["parakeet-gpu<br/>CUDA :5092<br/>istupakov/onnx"]
  end
  Docker[("Docker Engine")]
  HF[("HuggingFace<br/>model cache")]

  Client -- "JSON-RPC over stdio" --> Server
  Server -- "multipart/form-data" --> Backend
  Server -- "http" --> Cache
  Server -- "in-process" --> Metrics
  Server -- "subprocess" --> Boot
  Boot -- "docker CLI" --> Docker
  Docker -- "run/pull" --> Backend
  Backend -- "download" --> HF
  Backend -- "transcribed text" --> Server
  Server -- "JSON-RPC result" --> Client
```

## Layered architecture

```mermaid
flowchart TB
  L1["Layer 1: MCP Protocol<br/>stdio transport + JSON-RPC 2.0<br/>───<br/>mcp[cli]"]
  L2["Layer 2: Tool Surface<br/>7 @mcp.tool() functions<br/>───<br/>server.py:health/transcribe_*/record_*/get_metrics/clear_cache/bootstrap_parakeet"]
  L3["Layer 3: Domain Logic<br/>SSRF guard, VAD, cache, metrics<br/>───<br/>server.py:Validation/VAD/Cache/Metrics sections"]
  L4["Layer 4: Backend Adapters<br/>HTTP multipart POST + retry<br/>───<br/>server.py:_post_multipart, _get_safe_opener"]
  L5["Layer 5: External Backends<br/>Parakeet TDT (CPU/CUDA) via Docker<br/>───<br/>localhost:8022 or :5092"]

  L1 --> L2 --> L3 --> L4 --> L5
```

Each layer has **one job** and depends only on the layer below it.

## Module map

| File | Lines | Responsibility | Public surface |
|---|---|---|---|
| `server.py` | 1155 | MCP server, all 7 tools, domain logic | `mcp`, 7 `@mcp.tool()` |
| `parakeet_bootstrap.py` | 342 | Docker lifecycle, idempotent | `bootstrap()`, `main()` |
| `cli/whisper` | 112 | Standalone CLI using server as library | `main()` |
| `tests/test_parakeet_bootstrap.py` | 237 | Unit tests for bootstrap | 26 pytest cases |
| `pyproject.toml` | 80 | Build config, deps, ruff/mypy settings | — |

## Data flow — transcription

```mermaid
sequenceDiagram
  participant C as MCP Client
  participant S as server.py
  participant K as LRUCache
  participant M as Metrics
  participant B as Backend (Parakeet)
  participant D as Docker

  C->>S: transcribe_file(path, language, model)
  S->>S: validate (size, format, path exists)
  S->>K: cache_key(path, model, language, format)
  K-->>S: hit → return cached result
  Note over S: Cache miss
  S->>S: optional VAD trim
  S->>M: record_request(start)
  S->>B: POST /v1/audio/transcriptions (multipart)
  B-->>S: {text, language, duration, segments}
  S->>K: put(cache_key, result)
  S->>M: record_request(success, latency_ms)
  S-->>C: result + _meta
```

## Data flow — bootstrap

```mermaid
sequenceDiagram
  participant S as MCP Client
  participant B as parakeet_bootstrap
  participant D as Docker

  S->>B: bootstrap_parakeet() / python -m parakeet_bootstrap
  B->>D: docker info (check daemon)
  alt Daemon stopped
    B->>D: systemctl start / Start-Service
  end
  B->>D: docker image inspect (parakeet-tdt:ptbr-cpu)
  alt Missing
    B->>D: docker pull (download ~1GB)
  end
  B->>D: docker inspect (parakeet-ptbr)
  alt Missing
    B->>D: docker run -d --restart unless-stopped -p 8022:8022
  end
  loop Every 2s, up to 3 min
    B->>D: GET /health
    alt Returns {"status":"healthy"}
      B-->>S: ok=true, reused=...
    end
  end
  B-->>S: BootstrapResult(ok, steps, error)
```

## Security model

| Concern | Defense |
|---|---|
| SSRF (private IP access via `transcribe_url`) | `_is_private_host()` blocks `127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16` |
| SSRF via HTTP redirect | `_SafeRedirectHandler` revalidates each 3xx hop |
| File path traversal | `Path(path).expanduser().resolve()` + `is_file()` check |
| Large file DoS | `ORELHIA_MAX_BYTES` (default 25 MB) |
| FD leak | `os.close(fd)` after `tempfile.mkstemp` |
| Pipe-to-shell RCE | `curl \| sh` replaced with download + size cap + exec |
| Temp file cleanup | `TemporaryDirectory` context manager (guaranteed) |
| MIME confusion | Explicit extension → MIME map (`.ogg`/`.opus`/`webm`) |

## Performance budget

| Operation | Typical latency (1st call) | Cached/2nd call |
|---|---|---|
| `transcribe_file` (1MB MP3, CPU) | ~2-3s | <1ms |
| `transcribe_file` (1MB MP3, GPU) | ~2s | <1ms |
| `transcribe_url` (download + transcribe) | ~3-5s | n/a |
| `record_audio` (5s @ 16kHz) | ~8s (record + transcribe) | n/a |
| `transcribe_file` with VAD (1MB WAV) | ~2s (after trim) | <1ms |
| `health()` | ~50ms | n/a |
| `bootstrap_parakeet()` (idempotent, image cached) | <2s | n/a |
| `bootstrap_parakeet()` (cold, first install) | ~5-10min | n/a |

## Deployment topology

```mermaid
graph TB
  subgraph "User machine"
    direction TB
    Pi["pi (coding agent)"]
    MCP["orelhIA (Python)"]
    subgraph "Docker"
      CPU["parakeet-ptbr<br/>CPU :8022"]
      GPU["parakeet-gpu<br/>CUDA :5092"]
    end
    Cache[("~/.orelhIA/cache/")]
    Models[("HF cache<br/>~/.cache/huggingface/hub/")]
  end

  Pi -- "JSON-RPC stdio" --> MCP
  MCP -- "loopback" --> CPU
  MCP -- "loopback" --> GPU
  MCP --> Cache
  CPU --> Models
  GPU --> Models
```

## Why these choices

**Why stdlib-only where possible?**
- `urllib` instead of `requests` → fewer deps, no `requests-ossrf` middleware
- `tempfile` + `pathlib` instead of `shutil` hacks
- `json` + `dataclass` instead of `pydantic` overhead

**Why Docker over venv?**
- Parakeet TDT has heavy C++/CUDA deps (onnxruntime, cudnn) — not portable
- Docker image encapsulates model + runtime + system libs
- Idempotent restart semantics align with MCP lifecycle

**Why LRU cache over SQLite?**
- Audio is big (1-25MB), not many entries fit
- JSON index is human-readable, easy to inspect
- No migration story needed (one file)

**Why energy-based VAD over Silero/webrtcvad?**
- Zero new deps (stdlib struct.unpack)
- Sufficient for "remove silence before transcribe" use case
- Tradeoff: less accurate than ML-based, but <1ms vs 50ms for Silero
