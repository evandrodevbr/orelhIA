# Contributing

Thank you for your interest in **orelhIA**! Contributions of all kinds are welcome — bug reports, feature requests, documentation, and pull requests.

## Development setup

```bash
# 1. Clone
git clone https://github.com/evandro/orelhIA.git
cd orelhIA

# 2. Install (editable mode with dev deps)
pip install -e ".[dev,record]"

# 3. Install pre-commit hooks (optional)
pre-commit install

# 4. Start the parakeet container (idempotent)
python -m parakeet_bootstrap
```

## Code style

- **Python 3.10+** (uses `from __future__ import annotations`, PEP 604 unions in server, `Optional` in bootstrap)
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
pytest -k test_health                     # by name
pytest --cov=orelhIA                  # with coverage
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

Found a security issue? **Do not** open a public issue. Email <evandro@example.com> with details. Critical issues get a fix within 48 hours.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](./LICENSE).
