"""Caminhos-base de _is_private_host, _vad_trim_wav e transcribe_file.

Fixam o comportamento atual (offline, sem backend) antes/depois de refatorar
essas funções para reduzir a complexidade ciclomática.
"""
import os
import socket
import struct
import wave
from pathlib import Path
from unittest import mock

import pytest

import server


def addrinfo(*ips: str) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


# ---------------------------------------------------------------------------
# _is_private_host
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["", "localhost", "LOCALHOST", "ip6-localhost", "ip6-loopback"])
def test_private_host_empty_and_local_names(host):
    with mock.patch.object(server.socket, "getaddrinfo") as resolve:
        assert server._is_private_host(host) is True
        resolve.assert_not_called()


@pytest.mark.parametrize(
    "ip", ["10.0.0.1", "127.0.0.1", "169.254.1.1", "224.0.0.1", "240.0.0.1", "0.0.0.0", "::1"]
)
def test_private_host_restricted_ip_literals(ip):
    with mock.patch.object(server.socket, "getaddrinfo") as resolve:
        assert server._is_private_host(ip) is True
        resolve.assert_not_called()


def test_private_host_public_ip_literal_is_allowed():
    with mock.patch.object(server.socket, "getaddrinfo") as resolve:
        assert server._is_private_host("8.8.8.8") is False
        resolve.assert_not_called()


def test_private_host_unresolvable_name_is_blocked():
    with mock.patch.object(server.socket, "getaddrinfo", side_effect=socket.gaierror):
        assert server._is_private_host("nao-existe.invalid") is True


def test_private_host_name_resolving_to_public_ip_is_allowed():
    with mock.patch.object(server.socket, "getaddrinfo", return_value=addrinfo("8.8.8.8")):
        assert server._is_private_host("example.com") is False


def test_private_host_name_resolving_to_any_private_ip_is_blocked():
    with mock.patch.object(
        server.socket, "getaddrinfo", return_value=addrinfo("8.8.8.8", "192.168.0.5")
    ):
        assert server._is_private_host("rebind.example.com") is True


def test_private_host_ignores_unparseable_resolved_address():
    with mock.patch.object(
        server.socket, "getaddrinfo", return_value=addrinfo("not-an-ip", "8.8.8.8")
    ):
        assert server._is_private_host("example.com") is False


# ---------------------------------------------------------------------------
# _vad_trim_wav (sr=1000 -> janela de 30 amostras; limiares fixados)
# ---------------------------------------------------------------------------
LOUD = 10000


@pytest.fixture
def vad(monkeypatch):
    monkeypatch.setattr(server, "VAD_RMS_THRESHOLD", 0.01)
    monkeypatch.setattr(server, "VAD_MIN_SPEECH_MS", 250)
    monkeypatch.setattr(server, "VAD_PAD_MS", 100)


def write_wav(path: Path, samples: list[int], rate: int = 1000, width: int = 2) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        if width == 2:
            audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        else:
            audio.writeframes(bytes(samples))
    return path


def trimmed_frames(path: Path) -> int:
    out = server._vad_trim_wav(path)
    assert out is not None
    try:
        with wave.open(str(out), "rb") as audio:
            return audio.getnframes()
    finally:
        out.unlink()


def test_vad_returns_none_for_unreadable_file(vad, tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"isto nao e um wav")
    assert server._vad_trim_wav(path) is None


def test_vad_returns_none_for_non_16bit_wav(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [100] * 300, width=1)
    assert server._vad_trim_wav(path) is None


def test_vad_returns_none_for_empty_wav(vad, tmp_path):
    assert server._vad_trim_wav(write_wav(tmp_path / "audio.wav", [])) is None


def test_vad_returns_none_when_window_is_zero_samples(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [LOUD] * 300, rate=30)
    assert server._vad_trim_wav(path) is None


def test_vad_returns_none_when_pcm_is_truncated(vad, tmp_path):
    with mock.patch.object(server, "_read_wav", return_value=(b"", 1000, 1, 300)):
        assert server._vad_trim_wav(tmp_path / "audio.wav") is None


def test_vad_returns_none_for_silence(vad, tmp_path):
    assert server._vad_trim_wav(write_wav(tmp_path / "audio.wav", [0] * 300)) is None


def test_vad_returns_none_when_speech_is_shorter_than_minimum(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [0] * 300 + [LOUD] * 150 + [0] * 300)
    assert server._vad_trim_wav(path) is None


def test_vad_returns_none_when_trailing_speech_is_shorter_than_minimum(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [0] * 300 + [LOUD] * 150)
    assert server._vad_trim_wav(path) is None


def test_vad_keeps_speech_in_the_middle_with_padding(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [0] * 300 + [LOUD] * 300 + [0] * 300)
    assert trimmed_frames(path) == 500  # frames 200..700


def test_vad_keeps_speech_that_runs_to_the_end(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [0] * 300 + [LOUD] * 300)
    assert trimmed_frames(path) == 400  # frames 200..600


def test_vad_keeps_separate_segments_without_the_gap(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [LOUD] * 300 + [0] * 600 + [LOUD] * 300)
    assert trimmed_frames(path) == 800  # frames 0..400 e 800..1200


def _failing_write(error: Exception):
    real_open = wave.open

    def fake_open(name, mode="rb"):
        if mode == "wb":
            raise error
        return real_open(name, mode)

    return fake_open


@pytest.mark.parametrize("error", [wave.Error("disk full"), OSError("read-only")])
def test_vad_write_failure_returns_none_and_removes_temp(vad, tmp_path, error):
    path = write_wav(tmp_path / "audio.wav", [LOUD] * 300)
    temp = tmp_path / "trimmed.wav"
    temp.write_bytes(b"")
    fd = os.open(str(temp), os.O_RDONLY)
    with mock.patch.object(server.tempfile, "mkstemp", return_value=(fd, str(temp))), \
            mock.patch.object(server.wave, "open", side_effect=_failing_write(error)):
        assert server._vad_trim_wav(path) is None
    assert not temp.exists()


def test_vad_write_failure_survives_failed_cleanup(vad, tmp_path):
    path = write_wav(tmp_path / "audio.wav", [LOUD] * 300)
    temp = tmp_path / "trimmed.wav"
    temp.write_bytes(b"")
    fd = os.open(str(temp), os.O_RDONLY)
    with mock.patch.object(server.tempfile, "mkstemp", return_value=(fd, str(temp))), \
            mock.patch.object(server.wave, "open", side_effect=_failing_write(wave.Error("x"))), \
            mock.patch.object(Path, "unlink", side_effect=OSError("busy")):
        assert server._vad_trim_wav(path) is None


# ---------------------------------------------------------------------------
# transcribe_file
# ---------------------------------------------------------------------------
@pytest.fixture
def audio(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CACHE", server.LRUCache(tmp_path / "cache", 10))
    monkeypatch.setattr(server, "METRICS", server.Metrics())
    return write_wav(tmp_path / "audio.wav", [LOUD] * 300)


def test_transcribe_file_success_populates_meta_metrics_and_cache(audio):
    with mock.patch.object(
        server, "_post_multipart", return_value={"text": " oi ", "language": "pt"}
    ):
        result = server.transcribe_file(str(audio), language="pt")
    assert result["text"] == "oi"
    assert result["source"] == str(audio.resolve())
    assert result["_meta"]["cache"] == "miss"
    assert result["_meta"]["preprocess"] == "none"
    assert result["_meta"]["model"] == server.DEFAULT_MODEL
    snap = server.METRICS.snapshot()
    assert snap["requests"]["success"] == 1
    assert snap["cache"]["misses"] == 1
    assert snap["bytes_processed"] == audio.stat().st_size
    assert server.CACHE.size() == 1


def test_transcribe_file_cache_hit_updates_metrics(audio):
    with mock.patch.object(server, "_post_multipart", return_value={"text": "oi"}) as post:
        server.transcribe_file(str(audio))
        hit = server.transcribe_file(str(audio))
    assert post.call_count == 1
    assert hit["_meta"]["cache"] == "hit"
    snap = server.METRICS.snapshot()
    assert snap["cache"]["hits"] == 1
    assert snap["requests"]["success"] == 2


def test_transcribe_file_missing_file(audio, tmp_path):
    result = server.transcribe_file(str(tmp_path / "nope.wav"))
    assert result["code"] == "file_not_found"
    assert server.METRICS.snapshot()["requests"]["error"] == 1


def test_transcribe_file_unsupported_extension(audio, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    result = server.transcribe_file(str(other))
    assert result["code"] == "unsupported_format"
    assert ".wav" in result["supported"]
    assert server.METRICS.snapshot()["requests"]["error"] == 1


def test_transcribe_file_oversized_counts_as_error(audio, monkeypatch):
    monkeypatch.setattr(server, "MAX_BYTES", 10)
    assert server.transcribe_file(str(audio))["code"] == "file_too_large"
    assert server.METRICS.snapshot()["requests"]["error"] == 1


def test_transcribe_file_backend_error_returns_payload_and_cleans_vad_temp(audio, tmp_path):
    trimmed = write_wav(tmp_path / "trim.wav", [LOUD] * 270)
    failure = server.WhisperError("http_error", "boom", status=500)
    with mock.patch.object(server, "_vad_trim_wav", return_value=trimmed), \
            mock.patch.object(server, "_post_multipart", side_effect=failure):
        result = server.transcribe_file(str(audio), preprocess="vad")
    assert result["code"] == "http_error"
    assert result["status"] == 500
    assert result["_meta"]["cache"] == "miss"
    assert not trimmed.exists()
    assert server.METRICS.snapshot()["requests"]["error"] == 1
    assert server.CACHE.size() == 0


def test_transcribe_file_vad_fallback_when_trim_is_not_possible(audio):
    with mock.patch.object(server, "_vad_trim_wav", return_value=None), \
            mock.patch.object(server, "_post_multipart", return_value={"text": "ok"}) as post:
        result = server.transcribe_file(str(audio), preprocess="vad")
    assert result["_meta"]["preprocess"] == "none"
    assert post.call_args.args[1] == audio.resolve()


def test_transcribe_file_vad_applied_reports_vad_and_uploads_trimmed(audio, tmp_path):
    trimmed = write_wav(tmp_path / "trim.wav", [LOUD] * 270)
    with mock.patch.object(server, "_vad_trim_wav", return_value=trimmed), \
            mock.patch.object(server, "_post_multipart", return_value={"text": "ok"}) as post:
        result = server.transcribe_file(str(audio), preprocess="vad")
    assert result["_meta"]["preprocess"] == "vad"
    assert post.call_args.args[1] == trimmed


def test_transcribe_file_cache_store_failure_is_ignored(audio):
    with mock.patch.object(server, "_post_multipart", return_value={"text": "ok"}), \
            mock.patch.object(server.CACHE, "put", side_effect=OSError("disk full")):
        assert server.transcribe_file(str(audio))["text"] == "ok"


# ---------------------------------------------------------------------------
# transcribe_url
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requested: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        return self.payload if size < 0 else self.payload[:size]


def fake_opener(response: FakeResponse):
    opener = mock.Mock()
    opener.open.return_value = response
    return opener


def test_transcribe_url_reads_at_most_the_size_limit(audio, monkeypatch):
    monkeypatch.setattr(server, "MAX_BYTES", 100)
    response = FakeResponse(b"x" * 10_000)
    with mock.patch.object(server, "_validate_url", return_value=None), \
            mock.patch.object(server, "_get_safe_opener", return_value=fake_opener(response)), \
            mock.patch.object(server, "_post_multipart") as post:
        result = server.transcribe_url("https://example.com/a.ogg")
    assert result["code"] == "file_too_large"
    assert result["max_bytes"] == 100
    assert response.requested == [101]
    post.assert_not_called()


def test_transcribe_url_within_limit_transcribes(audio, monkeypatch):
    monkeypatch.setattr(server, "MAX_BYTES", 100)
    response = FakeResponse(b"x" * 100)
    with mock.patch.object(server, "_validate_url", return_value=None), \
            mock.patch.object(server, "_get_safe_opener", return_value=fake_opener(response)), \
            mock.patch.object(server, "_post_multipart", return_value={"text": "ok"}):
        result = server.transcribe_url("https://example.com/a.ogg")
    assert result["text"] == "ok"
    assert server.METRICS.snapshot()["bytes_processed"] == 100
