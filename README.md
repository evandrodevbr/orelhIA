# orelhIA

**Servidor MCP (stdio) que transcreve áudio local em pt-BR com Parakeet TDT rodando na sua máquina, sem enviar nada para fora.**

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-servidor%20stdio-6E56CF)
![Backend](https://img.shields.io/badge/backend-Parakeet%20TDT-orange)
![Testes](https://img.shields.io/badge/testes-26%20passando-brightgreen)
![Licen%C3%A7a](https://img.shields.io/badge/licen%C3%A7a-MIT-green)

[🇧🇷 Português](./README.md) · [🇺🇸 English](./README.en.md) · [Arquitetura](./docs/ARCHITECTURE.md) · [Contribuindo](./CONTRIBUTING.md)

## Sobre

Transcrever áudio para texto hoje normalmente significa mandar o arquivo para uma API paga. O orelhIA resolve isso pelo lado local: é um servidor MCP que expõe ferramentas de transcrição para agentes (Claude, pi, IDEs) e conversa com um backend Parakeet TDT rodando em Docker na própria máquina. O agente pede `transcribe_file("/tmp/audio.ogg")` e recebe o texto, com cache em disco para não retranscrever o mesmo áudio, VAD opcional para cortar silêncio, métricas de uso e gravação direta do microfone.

## Como funciona

```
agente MCP (Claude, pi, IDE)
        │  JSON-RPC sobre stdio
        ▼
orelhIA (server.py, FastMCP, 7 tools)
        │  POST multipart para /v1/audio/transcriptions (API compatível com a OpenAI)
        ▼
backend de transcrição local (Parakeet TDT em Docker)
  parakeet-ptbr :8022  (CPU)   ou   parakeet-gpu :5092  (CUDA)
        ▲
        └── cache LRU em disco (~/.orelhIA/cache, chave = SHA-256 do áudio + parâmetros)
```

- O servidor fala MCP por stdio: nenhuma porta HTTP própria, o cliente MCP sobe o processo.
- `transcribe_file` valida (caminho, formato, tamanho), consulta o cache LRU pelo hash do conteúdo, aplica VAD de energia quando pedido e faz o POST multipart no backend, com retry em 5xx.
- `transcribe_url` baixa URLs http(s) públicas passando por um guard SSRF (redirects são revalidados a cada hop).
- `record_audio` grava do microfone via PyAudio, salva WAV PCM 16-bit e transcreve.
- `bootstrap_parakeet` (e `python -m parakeet_bootstrap`) cuida do ciclo Docker de forma idempotente: Docker presente, daemon rodando, imagem presente, container rodando, `/health` saudável.

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.10+ (testado em 3.13) |
| Protocolo | MCP sobre stdio, SDK `mcp[cli]` `>=1.0,<2` (FastMCP) |
| Backend de transcrição | Parakeet TDT 0.6B v3 (ONNX) em container Docker, servido por API compatível com `/v1/audio/transcriptions`; também funciona com backends Whisper compatíveis (ex.: Speaches) |
| Cache | LRU em disco (`~/.orelhIA/cache/index.json`), chave SHA-256 |
| VAD | Energy-based em stdlib (`struct` + `wave`), sem dependência extra |
| Gravação | PyAudio (extra `record`) |
| Testes | pytest + `unittest.mock` (sem rede e sem Docker) |
| Qualidade | ruff (lint/format) e mypy, configurados no `pyproject.toml` |
| Pacotes | uv (`uv sync`, `uv build`); `pip install -e .` também funciona |
| Bootstrap | `parakeet_bootstrap.py` chamando a CLI do Docker |

## Requisitos

- Python `>=3.10`
- Docker (Engine ou Desktop) com o daemon rodando, para o backend de transcrição
- Uma imagem do backend disponível na máquina (o repositório não inclui Dockerfile; o nome padrão é `parakeet-tdt:ptbr-cpu`, configurável)
- Espaço em disco para a imagem do backend (a imagem CPU usada nos testes tem ~10 GB)
- Opcional: PyAudio e um microfone, apenas para `record_audio` (extra `record`)

## Início rápido

Comandos abaixo executados e verificados neste repositório (Linux, Python 3.13, uv):

```bash
git clone https://github.com/evandrodevbr/orelhIA.git
cd orelhIA

# instala o projeto (editable) + deps de dev e gravação no .venv
uv sync --extra dev --extra record
# alternativa com pip:
# pip install -e ".[dev,record]"

# backend idempotente (Docker + imagem + container + health)
uv run python -m parakeet_bootstrap
# se a imagem/container/porta forem outros:
# uv run python -m parakeet_bootstrap --image parakeet-tdt:cpu --container parakeet-cpu --port 5092

# sanidade
uv run python -m orelhIA.cli --health

# transcrever
uv run python -m orelhIA.cli audio.ogg -l pt
```

Registre o servidor MCP no seu cliente (config testada com handshake `initialize` e `tools/list`):

```json
{
  "mcpServers": {
    "orelhIA": {
      "command": "uv",
      "args": ["--directory", "/caminho/para/orelhIA", "run", "python", "-m", "orelhIA"]
    }
  }
}
```

Atalhos do `Makefile` (testados): `make test`, `make lint`, `make health`, `make run` (MCP via stdio), `make bootstrap`. O `Taskfile.yml` é a alternativa cross-platform (YAML validado; o runner `task` não estava instalado no ambiente de verificação).

## Uso

### Tools MCP

| Tool | O que faz | Parâmetros |
|---|---|---|
| `health()` | Status do backend + features (tenta `/v1/models`, cai para `/health`) | nenhum |
| `transcribe_file(path, language?, model?, response_format?, preprocess?)` | Arquivo local para texto | `path` obrigatório; `response_format`: `json`/`text`/`srt`/`vtt`; `preprocess`: `none`/`vad` |
| `transcribe_url(url, language?, model?)` | Baixa URL http(s) pública e transcreve | `url` obrigatório |
| `record_audio(seconds, output_path?, language?, model?, sample_rate?)` | Grava do microfone e transcreve | `seconds` de 1 a 600 |
| `get_metrics()` | Contadores, latência, cache hit rate, bytes processados | nenhum |
| `clear_cache()` | Limpa o cache LRU e retorna quantas entradas saíram | nenhum |
| `bootstrap_parakeet(port?, image?, container?)` | Docker + imagem + container, idempotente | todos opcionais |

O servidor se identifica como `whisper` no handshake MCP (nome herdado das versões anteriores).

### CLI

```bash
python -m orelhIA.cli audio.ogg -l pt              # texto
python -m orelhIA.cli audio.wav --format json      # resultado completo
python -m orelhIA.cli audio.wav --preprocess vad   # corta silêncio antes
python -m orelhIA.cli https://host/audio.ogg --url -l pt
python -m orelhIA.cli --record 5 --save fala.wav   # microfone por 5s
python -m orelhIA.cli --health | --metrics | --clear-cache
```

| Flag | Efeito |
|---|---|
| `-l, --language` | Código ISO-639-1 (`pt`, `en`, ...) |
| `-m, --model` | Override do modelo do backend |
| `--url` | Trata o argumento como URL |
| `--format` | `text` (padrão), `json`, `srt`, `vtt` |
| `--preprocess` | `none` (padrão) ou `vad` |
| `--record N` / `--save PATH` | Grava N segundos do microfone / salva o WAV |

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `ORELHIA_BASE_URL` | `http://localhost:5092` | URL do backend |
| `ORELHIA_MODEL` | `istupakov/parakeet-tdt-0.6b-v3-onnx` | Modelo padrão enviado no POST |
| `ORELHIA_TIMEOUT` | `120` | Timeout HTTP (segundos) |
| `ORELHIA_MAX_BYTES` | `26214400` (25 MB) | Limite de tamanho do áudio |
| `ORELHIA_ALLOW_PRIVATE_URLS` | `false` | Libera URLs privadas no `transcribe_url` (só dev) |
| `ORELHIA_CACHE_DIR` | `~/.orelhIA/cache` | Diretório do cache LRU |
| `ORELHIA_CACHE_MAX_ENTRIES` | `128` | Máximo de entradas no cache |
| `ORELHIA_VAD_RMS_THRESHOLD` | `0.01` | Limiar RMS do VAD (escala 0 a 1) |
| `ORELHIA_VAD_MIN_SPEECH_MS` | `250` | Mínimo de fala contínua para manter |
| `ORELHIA_VAD_PAD_MS` | `100` | Padding antes/depois dos trechos de fala |
| `ORELHIA_RECORD_SAMPLE_RATE` | `16000` | Sample rate da gravação |
| `ORELHIA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Códigos de erro das tools

Erros voltam como resultado estruturado (`{"error": ..., "code": ...}`), sem derrubar o servidor: `file_not_found`, `unsupported_format`, `file_too_large`, `invalid_scheme`, `private_url_blocked`, `download_failed`, `connection_error`, `http_error`, `retry_exhausted`, `pyaudio_unavailable`, `invalid_duration`, `recording_failed`, `write_failed`, `unexpected_error`.

## Produção

Este é um servidor local por stdio: não há endpoint HTTP para publicar. O que existe para empacotar e rodar:

```bash
uv build                          # gera dist/orelhia-3.0.0-py3-none-any.whl (com orelhIA/, server.py, parakeet_bootstrap.py) e o sdist
pip install dist/orelhia-*.whl    # o pacote instalado importa de qualquer diretório
python -m orelhIA                 # sobe o MCP server via stdio (quem sobe é o cliente MCP)
```

O processo do backend é gerenciado pelo bootstrap idempotente (`bootstrap_parakeet()` ou `python -m parakeet_bootstrap`), com `--restart unless-stopped` no container.

## Estrutura do projeto

```
server.py                    MCP server (FastMCP) + as 7 tools, cache, VAD, métricas, SSRF guard
parakeet_bootstrap.py        ciclo de vida Docker (imagem, container, /health), idempotente
orelhIA/
├── __init__.py              reexports do server.py (compatibilidade)
├── __main__.py              entrypoint stdio: python -m orelhIA
└── cli/__main__.py          CLI standalone: python -m orelhIA.cli
tests/
└── test_parakeet_bootstrap.py  26 testes unitários do bootstrap (mocks, sem Docker)
docs/ARCHITECTURE.md         diagramas e decisões de arquitetura
Makefile / Taskfile.yml      atalhos dev/test/lint/run
install.sh / install.ps1     instaladores (uv + deps + bootstrap)
```

## Testes e verificação

```bash
uv run pytest                                    # 26 passed
uv run pytest tests/test_parakeet_bootstrap.py   # só o bootstrap
uv run ruff check server.py parakeet_bootstrap.py orelhIA/ tests/   # limpo
uv run mypy server.py parakeet_bootstrap.py                          # limpo
uv run python -m orelhIA.cli --health                                # api real do backend
```

O que os testes cobrem de fato: 26 casos unitários de `parakeet_bootstrap` (detecção de plataforma/distro, Docker, imagem, container, idempotência, callbacks) e 9 regressões de `server.py` (cache, limites e VAD mono/estéreo), com respostas simuladas, sem rede e sem Docker. Não há CI configurada. A integração real continua manual: subir o backend, `--health`, transcrever um arquivo e conferir `get_metrics`.

## Estado atual e limitações

- O repositório não inclui Dockerfile nem receita de build da imagem do backend. O `parakeet_bootstrap` assume que a imagem já existe na máquina; um `docker pull` do nome padrão (`parakeet-tdt:ptbr-cpu`) falha, porque não há registry público com esse nome. Use `--image`/`--container`/`--port` (ou os parâmetros do `bootstrap_parakeet`) para a imagem que você tiver.
- Sem CI no GitHub (nenhum workflow no repositório).
- Transcreve o arquivo completo; não há streaming nem transcrição incremental.
- `preprocess="vad"` só atua em WAV PCM 16-bit; para outros formatos o VAD é ignorado com log.
- `record_audio` depende do PyAudio (extra `record`) e de um dispositivo de entrada; sem fala, o texto volta vazio.
- `transcribe_url` aceita apenas http(s); redes privadas são bloqueadas por padrão (guard SSRF).
- Métricas são em memória e zeram a cada reinício; `get_metrics` e `clear_cache` não entram nos contadores.
- `uv.lock` não é versionado (`uv sync` resolve na hora); a única trava hoje é `mcp>=1.0,<2`.
- macOS não é suportado pelo bootstrap (Windows e Linux sim, por design).
- Sem suporte a autenticação no backend: o container é exposto em loopback.

## Documentação

| Documento | Conteúdo |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Diagramas (topologia, camadas, fluxos), modelo de segurança, decisões de design |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Setup de desenvolvimento, estilo, processo de PR |
| [`CHANGELOG.md`](CHANGELOG.md) | Histórico de versões |

## Licença

MIT, veja [`LICENSE`](LICENSE). Créditos: Parakeet TDT 0.6B v3 (NVIDIA NeMo), fine-tune pt-BR TAGARELA ([Alefiury](https://huggingface.co/alefiury)), [Speaches](https://github.com/speaches-ai/speaches), [MCP](https://modelcontextprotocol.io).

## Validação de cache e áudio (2026-09-30)

A chave do cache inclui backend, modelo, idioma, formato, pré-processamento e parâmetros do VAD. A leitura do áudio para o hash ocorre em blocos, e arquivos acima do limite são rejeitados antes dessa leitura. Respostas do cache preservam o caminho do arquivo atual e os metadados de modelo/pré-processamento.

O VAD preserva a última janela parcial e une intervalos com padding sobreposto, evitando repetir amostras. Arquivos temporários são removidos também quando o backend gera um erro inesperado.

`uv run pytest` passa 35 testes offline de bootstrap, cache e WAV mono/estéreo; `uv run ruff check .` e `uv run mypy server.py` validam o código. Esses testes usam respostas simuladas e não comprovam Docker, modelo Parakeet, GPU ou microfone em execução.
