"""Parakeet bootstrap — instala Docker, baixa imagem, roda container CPU.

Idempotente: pode rodar múltiplas vezes sem efeito colateral. Reporta cada
etapa via callbacks de progresso para o chamador (CLI ou MCP).

Suporta Windows e Linux (sem macOS). Detecta distro Linux e usa o gerenciador
de pacotes correto (apt/dnf/pacman/zypper).
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("parakeet-bootstrap")

# Constantes
DEFAULT_IMAGE = "parakeet-tdt:ptbr-cpu"
DEFAULT_CONTAINER = "parakeet-ptbr"
DEFAULT_PORT = 8022
HEALTH_TIMEOUT_S = 180  # 3 min para download de modelo
HEALTH_POLL_S = 2

ProgressCb = Callable[[str, int, str], None]  # (step, percent, message)


@dataclass
class BootstrapResult:
    ok: bool
    image: str = DEFAULT_IMAGE
    container: str = DEFAULT_CONTAINER
    port: int = DEFAULT_PORT
    steps: list[str] = field(default_factory=list)
    error: str | None = None
    installed_docker: bool = False
    reused_container: bool = False


def _emit(cb: ProgressCb | None, step: str, percent: int, msg: str) -> None:
    if cb:
        try:
            cb(step, percent, msg)
        except Exception:  # pragma: no cover
            pass
    # logger.info é silencioso por padrão (handler configurado pelo chamador)
    if not cb:
        logger.info("[%d%%] %s: %s", percent, step, msg)


def _run(cmd: list[str], *, check: bool = True, timeout: int = 300, **kw) -> subprocess.CompletedProcess:
    """Run a command, raising on failure if check=True."""
    logger.debug("exec: %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, timeout=timeout, capture_output=True, text=True, **kw)


# ─── Detecção de plataforma ────────────────────────────────────────────


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _linux_distro() -> str | None:
    """Retorna 'debian' | 'rhel' | 'arch' | 'suse' | None."""
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
    except FileNotFoundError:
        return None
    if "ubuntu" in content or "debian" in content:
        return "debian"
    if any(x in content for x in ("fedora", "rhel", "centos", "rocky", "almalinux")):
        return "rhel"
    if "arch" in content or "manjaro" in content:
        return "arch"
    if "suse" in content or "opensuse" in content:
        return "suse"
    return None


# ─── Instalação do Docker ───────────────────────────────────────────────


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _docker_daemon_running() -> bool:
    try:
        _run(["docker", "info"], check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _install_docker_windows() -> None:
    """Instala Docker Desktop no Windows via winget ou instrui download manual."""
    if shutil.which("winget"):
        logger.info("Instalando Docker Desktop via winget...")
        _run(["winget", "install", "-e", "--id", "Docker.DockerDesktop", "--accept-source-agreements", "--accept-package-agreements"], timeout=600)
    else:
        # Fallback: chocolatey
        if shutil.which("choco"):
            logger.info("Instalando Docker Desktop via choco...")
            _run(["choco", "install", "docker-desktop", "-y"], timeout=600)
        else:
            raise RuntimeError(
                "Docker não encontrado. Instale Docker Desktop manualmente: "
                "https://www.docker.com/products/docker-desktop/"
            )


def _install_docker_linux(distro: str) -> None:
    """Instala Docker Engine via script oficial (idempotente)."""
    # Script oficial funciona em todas as distros
    logger.info("Baixando script oficial de instalação do Docker...")
    # C3: baixa para arquivo local antes de executar, evita pipe-to-sh com RCE
    # se DNS for sequestrado. Validação de tamanho + escrita em tempfile.
    import tempfile as _tf
    import urllib.request as _ur
    with _tf.TemporaryDirectory() as td:
        script_path = Path(td) / "get-docker.sh"
        with _ur.urlopen("https://get.docker.com", timeout=60) as r:
            data = r.read()
            if len(data) > 5_000_000:  # 5MB sanity cap
                raise RuntimeError("get.docker.com script suspiciously large")
            script_path.write_bytes(data)
        _run(["sh", str(script_path)], timeout=600)
    # Garante que o daemon está rodando
    if not _docker_daemon_running():
        _run(["sudo", "systemctl", "start", "docker"], check=False, timeout=30)
        _run(["sudo", "systemctl", "enable", "docker"], check=False, timeout=30)


def _ensure_docker(cb: ProgressCb | None) -> None:
    """Garante que Docker está instalado e rodando."""
    _emit(cb, "docker.check", 5, "Verificando Docker...")
    if _has_docker() and _docker_daemon_running():
        _emit(cb, "docker.check", 15, "Docker já instalado e rodando")
        return
    if _has_docker() and not _docker_daemon_running():
        _emit(cb, "docker.check", 15, "Docker instalado mas daemon parado — tentando iniciar...")
        if _is_windows():
            _run(["powershell", "-Command", "Start-Service com.docker.service"], check=False, timeout=30)
        else:
            _run(["sudo", "systemctl", "start", "docker"], check=False, timeout=30)
        if _docker_daemon_running():
            _emit(cb, "docker.check", 15, "Daemon Docker iniciado")
            return
        raise RuntimeError("Docker instalado mas daemon não responde")
    _emit(cb, "docker.install", 10, "Docker não encontrado, instalando...")
    if _is_windows():
        _install_docker_windows()
    else:
        distro = _linux_distro()
        if not distro:
            raise RuntimeError("Distro Linux não suportada para auto-install. Instale Docker manualmente.")
        _install_docker_linux(distro)
    # Verifica
    if not (_has_docker() and _docker_daemon_running()):
        raise RuntimeError("Docker instalado mas não está respondendo. Reinicie e rode de novo.")
    _emit(cb, "docker.install", 20, "Docker instalado e rodando")


# ─── Imagem ─────────────────────────────────────────────────────────────


def _image_exists(name: str) -> bool:
    try:
        r = _run(["docker", "image", "inspect", name], check=True, timeout=10)
        return r.returncode == 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _ensure_image(image: str, cb: ProgressCb | None) -> None:
    _emit(cb, "image.check", 25, f"Verificando imagem {image}...")
    if _image_exists(image):
        _emit(cb, "image.check", 50, f"Imagem {image} já presente")
        return
    _emit(cb, "image.pull", 30, f"Baixando {image} (~1GB)...")
    _run(["docker", "pull", image], timeout=900)
    _emit(cb, "image.pull", 50, f"Imagem {image} baixada")


# ─── Container ──────────────────────────────────────────────────────────


def _container_running(name: str) -> bool:
    try:
        r = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=True, timeout=10)
        return r.stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def _container_exists(name: str) -> bool:
    try:
        _run(["docker", "inspect", name], check=True, timeout=10)
        return True
    except subprocess.CalledProcessError:
        return False


def _start_container(image: str, name: str, port: int, cb: ProgressCb | None) -> None:
    """Cria e inicia container com porta mapeada e restart policy."""
    if _container_exists(name):
        if not _container_running(name):
            _emit(cb, "container.start", 65, f"Iniciando container existente {name}...")
            _run(["docker", "start", name], timeout=60)
        else:
            _emit(cb, "container.start", 70, f"Container {name} já rodando")
        return
    _emit(cb, "container.create", 60, f"Criando container {name}...")
    _run([
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:{port}",
        image,
    ], timeout=120)
    _emit(cb, "container.create", 70, f"Container {name} criado e iniciando")


# ─── Health check ───────────────────────────────────────────────────────


def _wait_healthy(port: int, cb: ProgressCb | None) -> None:
    """Aguarda o endpoint /health ficar healthy."""
    import urllib.request
    url = f"http://localhost:{port}/health"
    _emit(cb, "health.wait", 75, f"Aguardando {url} ficar healthy...")
    deadline = time.time() + HEALTH_TIMEOUT_S
    attempt = 0
    last_err = ""
    while time.time() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    body = r.read().decode("utf-8", errors="replace")
                    if "healthy" in body:
                        _emit(cb, "health.wait", 95, f"healthy após {attempt} tentativas ({body[:80]})")
                        return
        except Exception as e:
            last_err = type(e).__name__
        if attempt % 10 == 0:
            _emit(cb, "health.wait", 75 + min(20, attempt // 10), f"ainda aguardando (tentativa {attempt})...")
        time.sleep(HEALTH_POLL_S)
    raise RuntimeError(f"Container não ficou healthy em {HEALTH_TIMEOUT_S}s. Último erro: {last_err}")


# ─── Entry point ────────────────────────────────────────────────────────


def bootstrap(
    *,
    image: str = DEFAULT_IMAGE,
    container: str = DEFAULT_CONTAINER,
    port: int = DEFAULT_PORT,
    progress: ProgressCb | None = None,
) -> BootstrapResult:
    """Roda o bootstrap completo. Idempotente.

    Args:
        image: nome da imagem Docker a usar
        container: nome do container
        port: porta TCP para mapear (host e container)
        progress: callback opcional (step, percent, message)

    Returns:
        BootstrapResult com ok=True em sucesso, ou ok=False com error preenchido.
    """
    result = BootstrapResult(ok=False, image=image, container=container, port=port)
    try:
        if _is_windows() or _linux_distro() is not None or platform.system() == "Linux":
            pass  # OK
        else:
            raise RuntimeError(f"Sistema não suportado: {platform.system()}. Apenas Windows e Linux.")
        if platform.system() == "Darwin":
            raise RuntimeError("macOS não é suportado por design.")

        _ensure_docker(progress)
        result.installed_docker = True  # Docker presente (pode ter sido antes)

        _ensure_image(image, progress)
        result.steps.append("image_ok")

        if _container_running(container):
            result.reused_container = True
            _emit(progress, "container.reuse", 80, f"Container {container} já rodando, reusando")
        else:
            _start_container(image, container, port, progress)
            result.steps.append("container_started")

        _wait_healthy(port, progress)
        result.steps.append("healthy")
        result.ok = True
        _emit(progress, "done", 100, f"Bootstrap completo — parakeet em http://localhost:{port}")
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.exception("bootstrap failed")
    return result


def main() -> int:
    """CLI entry: python -m parakeet_bootstrap [--port 8022] [--quiet]"""
    import argparse
    p = argparse.ArgumentParser(description="Instala e roda parakeet-ptbr via Docker")
    p.add_argument("--image", default=DEFAULT_IMAGE)
    p.add_argument("--container", default=DEFAULT_CONTAINER)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--quiet", "-q", action="store_true", help="suprime progresso no stdout")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def cli_progress(step: str, pct: int, msg: str) -> None:
        if not args.quiet:
            print(f"[{pct:3d}%] {step}: {msg}", flush=True)

    r = bootstrap(image=args.image, container=args.container, port=args.port, progress=cli_progress)
    if r.ok:
        print(f"\n[OK] parakeet rodando em http://localhost:{r.port}")
        print(f"   image: {r.image}, container: {r.container}")
        if r.reused_container:
            print("   (container reutilizado — não foi recriado)")
        return 0
    else:
        print(f"\n[FALHA] {r.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
