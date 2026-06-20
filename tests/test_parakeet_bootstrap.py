"""Testes unitários para parakeet_bootstrap.

Não exercita o bootstrap real (Docker) — só valida:
- Detecção de plataforma
- Detecção de Docker
- Idempotência do fluxo (reuso de container)
- Manipulação de erro
- Formato do BootstrapResult
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

# Adiciona o diretório raiz ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import parakeet_bootstrap as pb


# ─── Detecção de plataforma ────────────────────────────────────────────


def test_is_windows_true_on_win32():
    with mock.patch.object(pb.platform, "system", return_value="Windows"):
        assert pb._is_windows() is True


def test_is_windows_false_on_linux():
    with mock.patch.object(pb.platform, "system", return_value="Linux"):
        assert pb._is_windows() is False


def test_linux_distro_debian():
    fake = 'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"'
    with mock.patch("builtins.open", mock.mock_open(read_data=fake)):
        assert pb._linux_distro() == "debian"


def test_linux_distro_rhel():
    fake = 'NAME="Fedora Linux"\nID=fedora'
    with mock.patch("builtins.open", mock.mock_open(read_data=fake)):
        assert pb._linux_distro() == "rhel"


def test_linux_distro_arch():
    fake = 'NAME="Arch Linux"\nID=arch'
    with mock.patch("builtins.open", mock.mock_open(read_data=fake)):
        assert pb._linux_distro() == "arch"


def test_linux_distro_unknown():
    with mock.patch("builtins.open", side_effect=FileNotFoundError):
        assert pb._linux_distro() is None


# ─── Detecção de Docker ────────────────────────────────────────────────


def test_has_docker_true():
    with mock.patch.object(pb.shutil, "which", return_value="/usr/bin/docker"):
        assert pb._has_docker() is True


def test_has_docker_false():
    with mock.patch.object(pb.shutil, "which", return_value=None):
        assert pb._has_docker() is False


def test_docker_daemon_running_true():
    with mock.patch.object(pb, "_run") as m:
        m.return_value = mock.Mock(returncode=0)
        assert pb._docker_daemon_running() is True
        m.assert_called_once()


def test_docker_daemon_running_false_on_error():
    import subprocess
    with mock.patch.object(pb, "_run", side_effect=subprocess.CalledProcessError(1, "docker")):
        assert pb._docker_daemon_running() is False


def test_docker_daemon_running_false_on_timeout():
    import subprocess
    with mock.patch.object(pb, "_run", side_effect=subprocess.TimeoutExpired("docker", 10)):
        assert pb._docker_daemon_running() is False


# ─── Imagem e container ────────────────────────────────────────────────


def test_image_exists_true():
    with mock.patch.object(pb, "_run") as m:
        m.return_value = mock.Mock(returncode=0)
        assert pb._image_exists("foo:bar") is True


def test_image_exists_false_on_error():
    import subprocess
    with mock.patch.object(pb, "_run", side_effect=subprocess.CalledProcessError(1, "docker")):
        assert pb._image_exists("foo:bar") is False


def test_container_running_true():
    with mock.patch.object(pb, "_run") as m:
        m.return_value = mock.Mock(stdout="true\n")
        assert pb._container_running("c") is True


def test_container_running_false_when_stopped():
    with mock.patch.object(pb, "_run") as m:
        m.return_value = mock.Mock(stdout="false\n")
        assert pb._container_running("c") is False


def test_container_exists_true():
    with mock.patch.object(pb, "_run") as m:
        m.return_value = mock.Mock(returncode=0)
        assert pb._container_exists("c") is True


def test_container_exists_false():
    import subprocess
    with mock.patch.object(pb, "_run", side_effect=subprocess.CalledProcessError(1, "docker")):
        assert pb._container_exists("c") is False


# ─── Bootstrap (idempotência, fluxo) ──────────────────────────────────


def test_bootstrap_rejects_macos():
    with mock.patch.object(pb.platform, "system", return_value="Darwin"):
        r = pb.bootstrap()
    assert r.ok is False
    # Cai na checagem genérica de OS não suportado
    assert "não suportado" in r.error or "macOS" in r.error


def test_bootstrap_rejects_unknown_os():
    with mock.patch.object(pb.platform, "system", return_value="BeOS"):
        r = pb.bootstrap()
    assert r.ok is False
    assert "não suportado" in r.error


def test_bootstrap_idempotent_uses_existing_container(monkeypatch):
    """Quando o container já está rodando, deve reusar (sem pull/run)."""
    progress_calls = []

    def fake_cb(step, pct, msg):
        progress_calls.append((step, pct, msg))

    monkeypatch.setattr(pb, "_ensure_docker", lambda cb: None)
    monkeypatch.setattr(pb, "_ensure_image", lambda img, cb: None)
    monkeypatch.setattr(pb, "_container_running", lambda name: True)
    monkeypatch.setattr(pb, "_wait_healthy", lambda port, cb: None)
    monkeypatch.setattr(pb, "_is_windows", lambda: True)
    monkeypatch.setattr(pb.platform, "system", lambda: "Windows")

    r = pb.bootstrap(progress=fake_cb)
    assert r.ok is True
    assert r.reused_container is True
    # Deve ter emitido o evento container.reuse
    assert any("container.reuse" in step for step, _, _ in progress_calls)


def test_bootstrap_creates_new_container_when_missing(monkeypatch):
    """Quando o container não existe, deve criar e iniciar."""
    monkeypatch.setattr(pb, "_ensure_docker", lambda cb: None)
    monkeypatch.setattr(pb, "_ensure_image", lambda img, cb: None)
    monkeypatch.setattr(pb, "_container_running", lambda name: False)
    monkeypatch.setattr(pb, "_container_exists", lambda name: False)

    started = []

    def fake_start(image, name, port, cb):
        started.append((image, name, port))

    monkeypatch.setattr(pb, "_start_container", fake_start)
    monkeypatch.setattr(pb, "_wait_healthy", lambda port, cb: None)
    monkeypatch.setattr(pb, "_is_windows", lambda: True)
    monkeypatch.setattr(pb.platform, "system", lambda: "Windows")

    r = pb.bootstrap(port=9999, image="img:tag", container="myc")
    assert r.ok is True
    assert r.reused_container is False
    assert started == [("img:tag", "myc", 9999)]


def test_bootstrap_propagates_error_from_docker(monkeypatch):
    """Se Docker falhar, retorna ok=False com error preenchido."""
    monkeypatch.setattr(pb, "_is_windows", lambda: True)
    monkeypatch.setattr(pb.platform, "system", lambda: "Windows")

    def fake_ensure_docker(cb):
        raise RuntimeError("Docker daemon sumiu")

    monkeypatch.setattr(pb, "_ensure_docker", fake_ensure_docker)
    r = pb.bootstrap()
    assert r.ok is False
    assert "Docker daemon sumiu" in r.error


def test_bootstrap_result_defaults():
    """BootstrapResult deve ter defaults sensatos."""
    r = pb.BootstrapResult(ok=False)
    assert r.image == "parakeet-tdt:ptbr-cpu"
    assert r.container == "parakeet-ptbr"
    assert r.port == 8022
    assert r.steps == []
    assert r.error is None
    assert r.installed_docker is False
    assert r.reused_container is False


# ─── Progress callback ────────────────────────────────────────────────


def test_emit_calls_progress_cb():
    calls = []
    pb._emit(lambda s, p, m: calls.append((s, p, m)), "step1", 50, "halfway")
    assert calls == [("step1", 50, "halfway")]


def test_emit_swallows_callback_exception():
    """Callback quebrado não deve derrubar o bootstrap."""
    def bad_cb(s, p, m):
        raise ValueError("boom")
    # Não deve levantar
    pb._emit(bad_cb, "step", 50, "msg")


def test_emit_no_op_when_callback_none():
    # Não deve levantar
    pb._emit(None, "step", 50, "msg")
