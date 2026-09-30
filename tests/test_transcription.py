"""Regressões offline de cache, limites e pré-processamento de áudio."""
import struct
import wave
from pathlib import Path
from unittest import mock

import pytest

import server


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CACHE", server.LRUCache(tmp_path / "cache", 10))
    monkeypatch.setattr(server, "METRICS", server.Metrics())


def wav_file(path: Path, samples: list[int], channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(1000)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def test_cache_separates_vad_and_original_and_restores_source(tmp_path):
    original = wav_file(tmp_path / "original.wav", [10000] * 300)
    trimmed = wav_file(tmp_path / "trimmed.wav", [10000] * 270)
    with mock.patch.object(server, "_post_multipart", side_effect=[{"text": "original"}, {"text": "trimmed"}]) as post, \
            mock.patch.object(server, "_vad_trim_wav", return_value=trimmed):
        first = server.transcribe_file(str(original))
        second = server.transcribe_file(str(original), preprocess="vad")
        hit = server.transcribe_file(str(original), preprocess="vad")
    assert first["text"] == "original"
    assert second["text"] == hit["text"] == "trimmed"
    assert post.call_count == 2
    assert hit["source"] == str(original)
    assert hit["_meta"]["cache"] == "hit"
    assert hit["_meta"]["preprocess"] == "vad"
    assert hit["_meta"]["model"] == server.DEFAULT_MODEL
    assert not trimmed.exists()


def test_vad_settings_and_backend_affect_cache_identity(tmp_path, monkeypatch):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300)
    before = server._cache_key(path, "model", None, "json", "vad")
    monkeypatch.setattr(server, "VAD_PAD_MS", server.VAD_PAD_MS + 1)
    assert server._cache_key(path, "model", None, "json", "vad") != before
    original = server._cache_key(path, "model", None, "json")
    monkeypatch.setattr(server, "BASE_URL", "http://different.invalid")
    assert server._cache_key(path, "model", None, "json") != original


def test_cache_read_failure_does_not_break_successful_transcription(tmp_path):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300)
    with mock.patch.object(server, "_cache_key", side_effect=OSError("read failure")), \
            mock.patch.object(server, "_post_multipart", return_value={"text": "ok"}):
        assert server.transcribe_file(str(path))["text"] == "ok"
    assert server.CACHE.size() == 0


def test_invalid_preprocess_is_rejected(tmp_path):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300)
    with mock.patch.object(server, "_post_multipart") as post:
        assert server.transcribe_file(str(path), preprocess="unknown")["code"] == "invalid_preprocess"
        post.assert_not_called()


def test_oversized_file_is_rejected_before_hashing(tmp_path, monkeypatch):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300)
    monkeypatch.setattr(server, "MAX_BYTES", 10)
    with mock.patch.object(server, "_cache_key") as key:
        assert server.transcribe_file(str(path))["code"] == "file_too_large"
        key.assert_not_called()


def test_vad_temp_is_removed_on_unexpected_backend_error(tmp_path):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300)
    trimmed = wav_file(tmp_path / "trim.wav", [10000] * 270)
    with mock.patch.object(server, "_vad_trim_wav", return_value=trimmed), \
            mock.patch.object(server, "_post_multipart", side_effect=RuntimeError("offline")):
        result = server.transcribe_file(str(path), preprocess="vad")
    assert result["code"] == "unexpected_error"
    assert not trimmed.exists()


@pytest.mark.parametrize("channels", [1, 2])
def test_vad_keeps_partial_final_window(tmp_path, monkeypatch, channels):
    monkeypatch.setattr(server, "VAD_PAD_MS", 0)
    path = wav_file(tmp_path / "audio.wav", [10000] * 350 * channels, channels)
    trimmed = server._vad_trim_wav(path)
    assert trimmed is not None
    try:
        assert server._read_wav(trimmed) == server._read_wav(path)
    finally:
        trimmed.unlink()


def test_vad_merges_overlapping_padding_without_duplicating_audio(tmp_path):
    path = wav_file(tmp_path / "audio.wav", [10000] * 300 + [0] * 90 + [-10000] * 300)
    trimmed = server._vad_trim_wav(path)
    assert trimmed is not None
    try:
        assert server._read_wav(trimmed) == server._read_wav(path)
    finally:
        trimmed.unlink()
