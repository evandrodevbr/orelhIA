# Contributing

Thank you for your interest in **orelhIA**! Contributions of all kinds are welcome — bug reports, feature requests, documentation, and pull requests.

## Development setup

```bash
# 1. Clone
git clone https://github.com/evandrodevbr/orelhIA.git
cd orelhIA

# 2. Install (editable, com deps de dev e gravação)
pip install -e ".[dev,record]"
# ou, com uv (instala o projeto + extras no .venv):
uv sync --extra dev --extra record

# 3. Start the parakeet container (idempotent)
python -m parakeet_bootstrap
```

## Code style

- **Python 3.10+** (`from __future__ import annotations`, unions PEP 604)
- **Line length: 100** (configured in `pyproject.toml`)
- **Type hints everywhere** on public APIs
- **Docstrings** for all public functions/classes (Google style)
- **No shell=True** in `subprocess` — use list form

Run linting and type checks:

```bash
ruff check .
ruff format .
mypy server.py parakeet_bootstrap.py
```

## Testing

```bash
pytest                                    # all tests
pytest tests/test_parakeet_bootstrap.py   # just bootstrap
pytest -k bootstrap                       # by name
```

Tests use `pytest` with `unittest.mock` for subprocess isolation. No network or Docker is required.

## Pull request process

1. Fork the repo and create a feature branch (`git checkout -b feat/my-change`).
2. Make your changes with tests.
3. Ensure `pytest` passes and `ruff check` is clean.
4. Update `CHANGELOG.md` under the `Unreleased` section.
5. Open a PR with a clear description (motivation, what changed, how to test).

## Architecture decisions

Before opening a large PR, please open an issue describing the change. We follow an **architecture-first** approach — design discussions happen in issues, not in code review.

For significant changes, the flow is:

```
Spec  →  Plan  →  Atomic commits  →  Tests  →  Review  →  Merge
```

See `docs/ARCHITECTURE.md` for the current system design.

## Security

Found a security issue? **Do not** open a public issue. Email <evandrodevbr@users.noreply.github.com> with details.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
