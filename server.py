"""Whisper MCP Server v3.0.

MCP server (stdio) que transcreve áudio usando um backend Whisper local
(Speaches ou outro compatível com a OpenAI Transcription API).

Tools
-----
- ``health()`` — verifica se o backend responde em ``/v1/models``.
- ``transcribe_file(path, language?, model?, response_format?, preprocess?)``
  — arquivo local.
- ``transcribe_url(url, language?, model?)`` — baixa uma URL e transcreve.
- ``record_audio(seconds, output_path?, language?)`` — grava do microfone e
  transcreve.
- ``get_metrics()`` — retorna métricas de uso (contadores, latência).
- ``clear_cache()`` — limpa o cache local de transcrições.

Features
--------
- **VAD preprocessing** (`preprocess="vad"`): corta silêncio via energy-based VAD
  para reduzir latência e custo. Zero deps extras.
- **Cache LRU local** (`~/.orelhIA/cache/`): evita re-transcrever mesmo
  áudio usando SHA-256 do conteúdo.
- **Métricas** (`get_metrics()` tool): contadores de requests, success, errors,
  latência por modelo, por formato, cache hit rate.
- **SSRF guard**: bloqueia URLs para redes privadas por padrão.
- **Limite de tamanho** (25 MB Whisper limit).
- **Retry com backoff** em 5xx.
- **MIME map explícito** para ``.ogg``/``.opus``.
- **Cleanup de temp files** via ``TemporaryDirectory``.

Configuração
------------
Variáveis de ambiente:

- ``ORELHIA_BASE_URL``  (default ``http://localhost:5092``)
- ``ORELHIA_MODEL``     (default ``alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx``)
- ``ORELHIA_TIMEOUT``   (default ``120`` segundos)
- ``ORELHIA_MAX_BYTES`` (default ``26214400`` = 25 MB, limite do Parakeet)
- ``ORELHIA_ALLOW_PRIVATE_URLS`` (default ``false``)
- ``ORELHIA_CACHE_DIR``  (default ``~/.orelhIA/cache``)
- ``ORELHIA_CACHE_MAX_ENTRIES`` (default ``128``)
- ``ORELHIA_VAD_RMS_THRESHOLD`` (default ``0.01``, escala 0-1 do peak)
- ``ORELHIA_VAD_MIN_SPEECH_MS`` (default ``250``, mínimo de fala para considerar)
- ``ORELHIA_VAD_PAD_MS`` (default ``100``, padding antes/depois da fala)
- ``ORELHIA_RECORD_SAMPLE_RATE`` (default ``16000`` — Whisper prefere)
- ``ORELHIA_LOG_LEVEL``  (default ``INFO``)
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import parakeet_bootstrap

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("mcp não está instalado. Rode: pip install mcp", file=sys.stderr)
    raise


__version__ = "3.0.0"
logger = logging.getLogger("orelhIA")
logging.basicConfig(
    level=os.environ.get("ORELHIA_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("ORELHIA_BASE_URL", "http://localhost:5092").rstrip("/")
DEFAULT_MODEL = os.environ.get(
    "ORELHIA_MODEL", "istupakov/parakeet-tdt-0.6b-v3-onnx"
)
TIMEOUT = float(os.environ.get("ORELHIA_TIMEOUT", "120"))
MAX_BYTES = int(os.environ.get("ORELHIA_MAX_BYTES", str(25 * 1024 * 1024)))
ALLOW_PRIVATE_URLS = os.environ.get("ORELHIA_ALLOW_PRIVATE_URLS", "false").lower() in (
    "1",
    "true",
    "yes",
)

CACHE_DIR = Path(
    os.environ.get("ORELHIA_CACHE_DIR", str(Path.home() / ".orelhIA" / "cache"))
).expanduser()
CACHE_MAX_ENTRIES = int(os.environ.get("ORELHIA_CACHE_MAX_ENTRIES", "128"))

VAD_RMS_THRESHOLD = float(os.environ.get("ORELHIA_VAD_RMS_THRESHOLD", "0.01"))
VAD_MIN_SPEECH_MS = int(os.environ.get("ORELHIA_VAD_MIN_SPEECH_MS", "250"))
VAD_PAD_MS = int(os.environ.get("ORELHIA_VAD_PAD_MS", "100"))

RECORD_SAMPLE_RATE = int(os.environ.get("ORELHIA_RECORD_SAMPLE_RATE", "16000"))

SUPPORTED_FORMATS: dict[str, str] = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "whisper",
    instructions=(
        "Transcreve áudio usando um backend Whisper local. "
        "Use transcribe_file para caminhos locais, transcribe_url para URLs, "
        "record_audio para gravar do microfone. "
        "language é código ISO-639-1 opcional (ex.: 'pt', 'en'); autodetect se omitido. "
        "preprocess='vad' em transcribe_file remove silêncio antes de transcrever. "
        "get_metrics retorna contadores de uso."
    ),
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class WhisperError(Exception):
    """Erro retornável como tool result."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.payload = {"error": message, "code": code, **extra}

    def to_result(self) -> dict[str, Any]:
        return self.payload


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return WhisperError(code, message, **extra).to_result()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
@dataclass
class Metrics:
    """Contadores thread-safe de uso do MCP."""

    requests_total: int = 0
    requests_success: int = 0
    requests_error: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    bytes_processed: int = 0
    duration_total_ms: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)
    by_format: dict[str, int] = field(default_factory=dict)
    by_tool: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_request(self, tool: str, success: bool) -> None:
        with self._lock:
            self.requests_total += 1
            self.by_tool[tool] = self.by_tool.get(tool, 0) + 1
            if success:
                self.requests_success += 1
            else:
                self.requests_error += 1

    def record_latency(self, duration_ms: float, model: str | None = None) -> None:
        with self._lock:
            self.duration_total_ms += duration_ms
            if model:
                self.by_model[model] = self.by_model.get(model, 0) + 1

    def record_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def record_bytes(self, n: int) -> None:
        with self._lock:
            self.bytes_processed += n

    def record_format(self, fmt: str) -> None:
        with self._lock:
            self.by_format[fmt] = self.by_format.get(fmt, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg = (
                self.duration_total_ms / self.requests_total
                if self.requests_total
                else 0.0
            )
            return {
                "uptime_seconds": time.time() - _START_TIME,
                "requests": {
                    "total": self.requests_total,
                    "success": self.requests_success,
                    "error": self.requests_error,
                    "by_tool": dict(self.by_tool),
                },
                "cache": {
                    "hits": self.cache_hits,
                    "misses": self.cache_misses,
                    "hit_rate": (
                        self.cache_hits / (self.cache_hits + self.cache_misses)
                        if (self.cache_hits + self.cache_misses)
                        else 0.0
                    ),
                },
                "latency": {
                    "total_ms": self.duration_total_ms,
                    "avg_ms": avg,
                },
                "bytes_processed": self.bytes_processed,
                "by_model": dict(self.by_model),
                "by_format": dict(self.by_format),
            }


_START_TIME = time.time()
METRICS = Metrics()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
class LRUCache:
    """Cache disk-based LRU indexado por SHA-256 do conteúdo."""

    def __init__(self, cache_dir: Path, max_entries: int) -> None:
        self.cache_dir = cache_dir
        self.max_entries = max_entries
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._load_index()

    def _index_path(self) -> Path:
        return self.cache_dir / "index.json"

    def _load_index(self) -> None:
        idx = self._index_path()
        if idx.exists():
            try:
                data = json.loads(idx.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._index[k] = v
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("cache index corrupted, starting fresh: %s", exc)

    def _save_index(self) -> None:
        try:
            self._index_path().write_text(
                json.dumps(self._index, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("cache index save failed: %s", exc)

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._index.get(key)
            if entry is None:
                return None
            # Move to end (LRU)
            self._index.move_to_end(key)
            return entry

    def put(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._index[key] = entry
            self._index.move_to_end(key)
            while len(self._index) > self.max_entries:
                old_key, _ = self._index.popitem(last=False)
                logger.debug("cache evicted %s", old_key)
            self._save_index()

    def clear(self) -> int:
        with self._lock:
            n = len(self._index)
            self._index.clear()
            try:
                for p in self.cache_dir.glob("*.json"):
                    if p.name != "index.json":
                        p.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("cache clear failed: %s", exc)
            self._save_index()
            return n

    def size(self) -> int:
        with self._lock:
            return len(self._index)


CACHE = LRUCache(CACHE_DIR, CACHE_MAX_ENTRIES)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalida cada hop de redirect contra a lista de IPs privados.

    Previne C1 (SSRF via 302) e mitiga C2 (DNS rebinding em janela curta):
    cada destino é revalidado depois de resolvido para IP.
    """
    def http_error_302(self, req, fp, code, msg, headers, newurl):
        if not ALLOW_PRIVATE_URLS:
            err = _validate_url(newurl)
            if err is not None:
                raise urllib.error.URLError(
                    f"redirect blocked: {err.get('message', 'private host')}"
                )
        return super().http_error_302(req, fp, code, msg, headers, newurl)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302  # type: ignore[assignment]


def _get_safe_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    opener.addheaders = [("User-Agent", f"orelhIA/{__version__}")]
    return opener


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _is_private_host(host: str) -> bool:
    host_lower = (host or "").lower()
    if not host_lower:
        return True
    if host_lower in {"localhost", "ip6-localhost", "ip6-loopback"}:
        return True
    ip = None
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        pass
    if ip is not None:
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip2 = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip2.is_private
            or ip2.is_loopback
            or ip2.is_link_local
            or ip2.is_multicast
            or ip2.is_reserved
            or ip2.is_unspecified
        ):
            return True
    return False


def _validate_url(url: str) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return _err(
            "invalid_scheme",
            f"Esquema não suportado: {parsed.scheme!r}. Use http(s).",
        )
    if not ALLOW_PRIVATE_URLS and _is_private_host(parsed.hostname or ""):
        return _err(
            "private_url_blocked",
            "URLs para redes privadas são bloqueadas por padrão. "
            "Defina ORELHIA_ALLOW_PRIVATE_URLS=true para liberar.",
        )
    return None


# ---------------------------------------------------------------------------
# VAD (energy-based)
# ---------------------------------------------------------------------------
def _read_wav(path: Path) -> tuple[bytes, int, int, int] | None:
    """Lê arquivo WAV. Retorna (raw_pcm16, sample_rate, n_channels, n_frames) ou None."""
    try:
        with wave.open(str(path), "rb") as w:
            sr = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            nf = w.getnframes()
            if sw != 2:
                # só suportamos PCM 16-bit
                return None
            raw = w.readframes(nf)
        return raw, sr, nch, nf
    except (wave.Error, EOFError, OSError):
        return None


def _vad_trim_wav(path: Path) -> Path | None:
    """Aplica VAD energy-based em WAV PCM 16-bit.

    Estratégia: divide o áudio em janelas de 30ms, calcula RMS normalizado,
    marca como "speech" se RMS > threshold. Mantém segmentos contínuos
    de speech >= MIN_SPEECH_MS. Aplica padding antes/depois.

    Retorna path para WAV trimado, ou None se não foi possível.
    """
    data = _read_wav(path)
    if data is None:
        return None
    raw, sr, nch, nf = data
    if nf == 0:
        return None

    # 30ms windows
    window_size = int(sr * 0.030)
    if window_size == 0:
        return None
    n_windows = (nf + window_size - 1) // window_size

    # Calcular RMS por janela
    samples_per_window = window_size * nch
    rms_values: list[float] = []
    for i in range(n_windows):
        start = i * samples_per_window
        end = min(start + samples_per_window, nf * nch)
        if end * 2 > len(raw):
            break
        chunk = raw[start * 2 : end * 2]
        # unpack como int16 little-endian
        n_samples = len(chunk) // 2
        if n_samples == 0:
            break
        # vectorized RMS via struct unpack
        try:
            ints = struct.unpack(f"<{n_samples}h", chunk)
        except struct.error:
            break
        sq = sum(s * s for s in ints)
        rms = (sq / n_samples) ** 0.5
        # PCM int16 inclui -32768; use sua magnitude para manter a escala 0-1.
        rms_values.append(rms / 32768.0)

    if not rms_values:
        return None

    # Marcar janelas como speech
    threshold = VAD_RMS_THRESHOLD
    speech_flags = [r > threshold for r in rms_values]

    # Encontrar segmentos contínuos
    segments: list[tuple[int, int]] = []
    in_speech = False
    start = 0
    for i, s in enumerate(speech_flags):
        if s and not in_speech:
            start = i
            in_speech = True
        elif not s and in_speech:
            if (i - start) * window_size * 1000 >= VAD_MIN_SPEECH_MS * sr:
                segments.append((start, i))
            in_speech = False
    speech_frames = min(len(speech_flags) * window_size, nf) - start * window_size
    if in_speech and speech_frames * 1000 >= VAD_MIN_SPEECH_MS * sr:
        segments.append((start, len(speech_flags)))

    if not segments:
        return None

    # Aplicar padding e converter para samples
    pad_frames = max(0, sr * VAD_PAD_MS // 1000)
    padded_segments: list[tuple[int, int]] = []
    for s_start, s_end in segments:
        frame_start = max(0, s_start * window_size - pad_frames)
        frame_end = min(nf, s_end * window_size + pad_frames)
        # Unir padding sobreposto evita repetir fala/silêncio na transcrição.
        if padded_segments and frame_start <= padded_segments[-1][1]:
            padded_segments[-1] = (padded_segments[-1][0], max(padded_segments[-1][1], frame_end))
        else:
            padded_segments.append((frame_start, frame_end))

    if not padded_segments:
        return None

    out_raw = b"".join(raw[start * nch * 2 : end * nch * 2] for start, end in padded_segments)
    # Escrever WAV em temp file
    fd, name = tempfile.mkstemp(suffix=".wav", prefix="whisper-vad-")
    os.close(fd)  # C5: evita FD leak
    out_path = Path(name)
    try:
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(nch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(out_raw)
    except (wave.Error, OSError) as exc:
        logger.warning("VAD: failed to write trimmed WAV: %s", exc)
        try:
            out_path.unlink()
        except OSError:
            pass
        return None

    reduction = 1.0 - (len(out_raw) / len(raw))
    logger.info(
        "VAD: trimmed %s from %d to %d samples (%.1f%% reduction, %d segments)",
        path.name,
        nf,
        len(out_raw) // 2 // nch,
        reduction * 100,
        len(padded_segments),
    )
    return out_path


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _post_multipart(
    url: str,
    file_path: Path,
    *,
    model: str,
    language: str | None,
    response_format: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    file_bytes = file_path.read_bytes()
    if len(file_bytes) > MAX_BYTES:
        raise WhisperError(
            "file_too_large",
            f"Arquivo tem {len(file_bytes)} bytes; máximo permitido é {MAX_BYTES}.",
            max_bytes=MAX_BYTES,
        )

    suffix = file_path.suffix.lower()
    mime = SUPPORTED_FORMATS.get(suffix) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    boundary = f"----orelhIA-{os.getpid()}-{int(time.time() * 1000)}"
    parts: list[bytes] = []
    for name, value in (
        ("model", model),
        ("response_format", response_format),
    ):
        if value:
            parts.append(f'--{boundary}\r\n'.encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(f"{value}\r\n".encode())
    if language:
        parts.append(f'--{boundary}\r\n'.encode())
        parts.append(
            b'Content-Disposition: form-data; name="language"\r\n\r\n'
        )
        parts.append(f"{language}\r\n".encode())
    parts.append(f'--{boundary}\r\n'.encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return json.loads(raw)
                return {"text": raw}
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600 and attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                last_exc = exc
                continue
            raise WhisperError(
                "http_error",
                f"Backend retornou HTTP {exc.code}: {exc.reason}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise WhisperError(
                "connection_error",
                f"Falha de conexão: {exc.reason}",
            ) from exc
    raise WhisperError("retry_exhausted", f"Falhou após {max_retries + 1} tentativas: {last_exc}")


def _normalize(data: dict[str, Any], source: str) -> dict[str, Any]:
    text = (data.get("text") or "").strip()
    return {
        "text": text,
        "language": data.get("language"),
        "duration": data.get("duration"),
        "segments": data.get("segments") or [],
        "source": source,
    }


def _cache_key(
    file_path: Path, model: str, language: str | None, response_format: str,
    preprocess: str = "none",
) -> str:
    """SHA-256 do conteúdo + params de transcrição."""
    params: dict[str, Any] = {
        "backend": BASE_URL, "model": model, "language": language,
        "format": response_format, "preprocess": preprocess,
    }
    if preprocess == "vad":
        params["vad"] = [VAD_RMS_THRESHOLD, VAD_MIN_SPEECH_MS, VAD_PAD_MS]
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode())
    digest.update(b"\0")
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def health() -> dict[str, Any]:
    """Verifica se o backend está respondendo. Tenta ``/v1/models`` (Whisper) e cai pra ``/health`` (Parakeet)."""
    payload: dict = {}
    for path in ("/v1/models", "/health"):
        try:
            with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw) if raw else {}
                if payload:
                    break
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
    if not payload:
        METRICS.record_request("health", success=False)
        return {
            "ok": False,
            "base_url": BASE_URL,
            "default_model": DEFAULT_MODEL,
            "version": __version__,
            "error": "backend não respondeu em /v1/models nem /health",
        }
    # Normaliza payload: Parakeet (/health tem 'models') e Whisper (/v1/models tem 'data')
    models = (
        [m.get("id") for m in payload.get("data", []) if m.get("id")]
        or payload.get("models", [])
    )
    METRICS.record_request("health", success=True)
    return {
        "ok": True,
        "base_url": BASE_URL,
        "default_model": DEFAULT_MODEL,
        "version": __version__,
        "features": {
            "vad_preprocessing": True,
            "cache": True,
            "metrics": True,
            "recording": _recording_available(),
        },
        "models": models,
    }


@mcp.tool()
def transcribe_file(
    path: str,
    language: str | None = None,
    model: str | None = None,
    response_format: str = "json",
    preprocess: str = "none",
) -> dict[str, Any]:
    """Transcreve um arquivo de áudio local com cache e VAD opcional.

    Args:
        path: Caminho absoluto do arquivo de áudio.
        language: Código ISO-639-1 opcional (ex.: ``"pt"``, ``"en"``).
        model: Override do modelo.
        response_format: ``"json"`` (default), ``"text"``, ``"srt"`` ou ``"vtt"``.
        preprocess: ``"none"`` (default) ou ``"vad"`` (corta silêncio).

    Returns:
        ``dict`` com ``text``, ``language``, ``duration``, ``segments``, ``source``
        e ``_meta`` (cache, preprocessing, latency).
    """
    started = time.time()
    METRICS.record_format(response_format)
    if preprocess not in ("none", "vad"):
        METRICS.record_request("transcribe_file", success=False)
        return _err("invalid_preprocess", "preprocess deve ser 'none' ou 'vad'.")

    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        METRICS.record_request("transcribe_file", success=False)
        return _err("file_not_found", f"Arquivo não encontrado: {path}")
    if file_path.suffix.lower() not in SUPPORTED_FORMATS:
        METRICS.record_request("transcribe_file", success=False)
        return _err(
            "unsupported_format",
            f"Formato não suportado: {file_path.suffix!r}.",
            supported=sorted(SUPPORTED_FORMATS),
        )

    chosen_model = model or DEFAULT_MODEL

    # 1. Cache check
    cache_key = None
    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_BYTES:
            METRICS.record_request("transcribe_file", success=False)
            return _err("file_too_large", f"Arquivo tem {file_size} bytes; máximo permitido é {MAX_BYTES}.", max_bytes=MAX_BYTES)
        cache_key = _cache_key(file_path, chosen_model, language, response_format, preprocess)
        cached = CACHE.get(cache_key)
        if cached is not None:
            METRICS.record_cache(hit=True)
            METRICS.record_request("transcribe_file", success=True)
            elapsed_ms = (time.time() - started) * 1000
            METRICS.record_latency(elapsed_ms, chosen_model)
            logger.info("cache hit for %s", file_path.name)
            return {
                **cached, "source": str(file_path),
                "_meta": {**cached.get("_meta", {}), "cache": "hit", "latency_ms": elapsed_ms},
            }
    except OSError as exc:
        logger.warning("cache lookup failed: %s", exc)
    METRICS.record_cache(hit=False)

    # 2. VAD preprocess
    work_path = file_path
    vad_applied = False
    if preprocess == "vad":
        trimmed = _vad_trim_wav(file_path)
        if trimmed is not None:
            work_path = trimmed
            vad_applied = True
        else:
            logger.info(
                "VAD skipped for %s (não é WAV PCM 16-bit ou vazio)",
                file_path.name,
            )

    # 3. Transcribe
    try:
        data = _post_multipart(
            f"{BASE_URL}/v1/audio/transcriptions",
            work_path,
            model=chosen_model,
            language=language,
            response_format=response_format,
        )
    except WhisperError as exc:
        METRICS.record_request("transcribe_file", success=False)
        elapsed_ms = (time.time() - started) * 1000
        METRICS.record_latency(elapsed_ms, chosen_model)
        result = exc.to_result()
        result["_meta"] = {"cache": "miss", "latency_ms": elapsed_ms}
        return result
    except Exception as exc:
        logger.exception("transcribe_file failed")
        METRICS.record_request("transcribe_file", success=False)
        return _err(
            "unexpected_error",
            f"Erro inesperado: {type(exc).__name__}: {exc}",
        )
    finally:
        if vad_applied:
            try:
                work_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("VAD temp cleanup failed: %s", exc)

    elapsed_ms = (time.time() - started) * 1000
    METRICS.record_latency(elapsed_ms, chosen_model)
    METRICS.record_bytes(file_path.stat().st_size)
    METRICS.record_request("transcribe_file", success=True)

    result = _normalize(data, str(file_path))
    result["_meta"] = {
        "cache": "miss",
        "preprocess": "vad" if vad_applied else "none",
        "latency_ms": elapsed_ms,
        "model": chosen_model,
    }

    # 4. Cache store
    try:
        if cache_key is not None:
            CACHE.put(cache_key, {k: v for k, v in result.items() if k != "source"})
    except OSError as exc:
        logger.warning("cache store failed: %s", exc)

    return result


@mcp.tool()
def transcribe_url(
    url: str,
    language: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Baixa uma URL http(s) e transcreve o áudio."""
    started = time.time()
    METRICS.record_format("json")

    if err := _validate_url(url):
        METRICS.record_request("transcribe_url", success=False)
        return err

    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name or "audio.ogg"
    suffix = Path(name).suffix.lower() or ".ogg"
    if suffix not in SUPPORTED_FORMATS:
        suffix = ".ogg"
    chosen_model = model or DEFAULT_MODEL

    try:
        with tempfile.TemporaryDirectory(prefix="orelhIA-") as tmpdir:
            tmp_path = Path(tmpdir) / f"audio{suffix}"
            try:
                with _get_safe_opener().open(url, timeout=TIMEOUT) as resp:
                    data_bytes = resp.read()
                if len(data_bytes) > MAX_BYTES:
                    return _err(
                        "file_too_large",
                        f"Arquivo remoto tem {len(data_bytes)} bytes; máximo {MAX_BYTES}.",
                        max_bytes=MAX_BYTES,
                    )
                tmp_path.write_bytes(data_bytes)
            except urllib.error.URLError as exc:
                METRICS.record_request("transcribe_url", success=False)
                return _err("download_failed", f"Falha ao baixar URL: {exc.reason}")

            try:
                data = _post_multipart(
                    f"{BASE_URL}/v1/audio/transcriptions",
                    tmp_path,
                    model=chosen_model,
                    language=language,
                    response_format="json",
                )
            except WhisperError as exc:
                METRICS.record_request("transcribe_url", success=False)
                elapsed_ms = (time.time() - started) * 1000
                METRICS.record_latency(elapsed_ms, chosen_model)
                result = exc.to_result()
                result["_meta"] = {"cache": "miss", "latency_ms": elapsed_ms}
                return result

            elapsed_ms = (time.time() - started) * 1000
            METRICS.record_latency(elapsed_ms, chosen_model)
            METRICS.record_bytes(len(data_bytes))
            METRICS.record_request("transcribe_url", success=True)
            result = _normalize(data, url)
            result["_meta"] = {
                "cache": "miss",
                "latency_ms": elapsed_ms,
                "model": chosen_model,
            }
            return result
    except Exception as exc:
        logger.exception("transcribe_url failed")
        METRICS.record_request("transcribe_url", success=False)
        return _err(
            "unexpected_error",
            f"Erro inesperado: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def _recording_available() -> bool:
    try:
        import pyaudio  # noqa: F401

        return True
    except ImportError:
        return False


def _record_audio_pyaudio(
    seconds: int,
    sample_rate: int,
    channels: int = 1,
) -> bytes:
    """Grava PCM 16-bit mono via PyAudio. Retorna raw bytes."""
    import pyaudio

    if seconds <= 0 or seconds > 600:
        raise WhisperError(
            "invalid_duration",
            f"seconds deve estar entre 1 e 600 (foi {seconds}).",
        )

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=int(sample_rate * 0.05),  # 50ms
        )
        frames: list[bytes] = []
        chunk_size = int(sample_rate * 0.05)
        n_chunks = int(seconds / 0.05)
        logger.info("recording %ds at %dHz...", seconds, sample_rate)
        for _ in range(n_chunks):
            data = stream.read(chunk_size, exception_on_overflow=False)
            frames.append(data)
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()
    return b"".join(frames)


def _write_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> Path:
    """Escreve PCM 16-bit em arquivo WAV temporário."""
    fd, name = tempfile.mkstemp(suffix=".wav", prefix="whisper-rec-")
    os.close(fd)  # C5: evita FD leak
    path = Path(name)
    try:
        with wave.open(str(path), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
    except (wave.Error, OSError) as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise WhisperError("write_failed", f"Falha ao escrever WAV: {exc}") from exc
    return path


@mcp.tool()
def record_audio(
    seconds: int,
    output_path: str | None = None,
    language: str | None = None,
    model: str | None = None,
    sample_rate: int = 16000,
) -> dict[str, Any]:
    """Grava do microfone local e transcreve o áudio.

    Args:
        seconds: Duração da gravação em segundos (1-600).
        output_path: Caminho para salvar o WAV. Se omitido, usa temp file.
        language: Código ISO-639-1 opcional.
        model: Override do modelo.
        sample_rate: Sample rate (default 16000, ideal para Whisper).

    Returns:
        ``dict`` com ``text``, ``duration``, ``recorded_path``, ``_meta``.
    """
    started = time.time()
    METRICS.record_format("wav")

    if not _recording_available():
        METRICS.record_request("record_audio", success=False)
        return _err(
            "pyaudio_unavailable",
            "PyAudio não está instalado. Rode: pip install pyaudio",
        )

    try:
        pcm = _record_audio_pyaudio(seconds, sample_rate)
    except WhisperError as exc:
        METRICS.record_request("record_audio", success=False)
        return exc.to_result()
    except Exception as exc:
        logger.exception("recording failed")
        METRICS.record_request("record_audio", success=False)
        return _err(
            "recording_failed",
            f"Falha ao gravar: {type(exc).__name__}: {exc}",
        )

    # Salvar WAV
    if output_path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        wav_path = _write_wav_to(pcm, sample_rate, out)
    else:
        wav_path = _write_wav(pcm, sample_rate)

    try:
        chosen_model = model or DEFAULT_MODEL
        data = _post_multipart(
            f"{BASE_URL}/v1/audio/transcriptions",
            wav_path,
            model=chosen_model,
            language=language,
            response_format="json",
        )
    except WhisperError as exc:
        METRICS.record_request("record_audio", success=False)
        result = exc.to_result()
        result["_meta"] = {"recorded_path": str(wav_path)}
        return result

    elapsed_ms = (time.time() - started) * 1000
    METRICS.record_latency(elapsed_ms, chosen_model)
    METRICS.record_bytes(len(pcm))
    METRICS.record_request("record_audio", success=True)

    result = _normalize(data, str(wav_path))
    result["_meta"] = {
        "cache": "miss",
        "latency_ms": elapsed_ms,
        "model": chosen_model,
        "recorded_path": str(wav_path),
        "duration_seconds": seconds,
        "sample_rate": sample_rate,
    }
    return result


def _write_wav_to(pcm: bytes, sample_rate: int, out: Path) -> Path:
    """Escreve WAV em path específico."""
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        return out
    except (wave.Error, OSError) as exc:
        raise WhisperError("write_failed", f"Falha ao escrever WAV: {exc}") from exc


# ---------------------------------------------------------------------------
# Cache + metrics tools
# ---------------------------------------------------------------------------
@mcp.tool()
def get_metrics() -> dict[str, Any]:
    """Retorna métricas de uso: contadores, latência, cache hit rate, etc."""
    return {
        "version": __version__,
        "base_url": BASE_URL,
        "default_model": DEFAULT_MODEL,
        "cache": {
            "dir": str(CACHE_DIR),
            "entries": CACHE.size(),
            "max_entries": CACHE_MAX_ENTRIES,
        },
        "metrics": METRICS.snapshot(),
    }


@mcp.tool()
def bootstrap_parakeet(
    port: int = 8022,
    image: str = "parakeet-tdt:ptbr-cpu",
    container: str = "parakeet-ptbr",
) -> dict[str, Any]:
    """Instala e inicia o container Docker do Parakeet (transcrição local).

    Idempotente: pode rodar múltiplas vezes sem efeito colateral. Instala
    Docker se faltar, baixa a imagem, e garante o container rodando em
    http://localhost:{port}. Funciona em Windows e Linux (macOS não suportado).

    Args:
        port: porta TCP (default 8022)
        image: nome da imagem Docker (default parakeet-tdt:ptbr-cpu)
        container: nome do container (default parakeet-ptbr)

    Returns:
        dict com ok, image, container, port, steps, error, reused_container
    """
    progress_log: list[str] = []

    def cb(step: str, pct: int, msg: str) -> None:
        progress_log.append(f"[{pct:3d}%] {step}: {msg}")

    r = parakeet_bootstrap.bootstrap(
        image=image,
        container=container,
        port=port,
        progress=cb,
    )
    return {
        "ok": r.ok,
        "image": r.image,
        "container": r.container,
        "port": r.port,
        "steps": r.steps,
        "error": r.error,
        "reused_container": r.reused_container,
        "progress": progress_log,
    }


@mcp.tool()
def clear_cache() -> dict[str, Any]:
    """Limpa o cache local de transcrições. Retorna número de entries removidas."""
    n = CACHE.clear()
    return {"removed": n}


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(
        "orelhIA v%s starting (backend=%s, model=%s, cache=%s)",
        __version__,
        BASE_URL,
        DEFAULT_MODEL,
        CACHE_DIR,
    )
    mcp.run()
