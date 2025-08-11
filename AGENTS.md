# Repository Guidelines

## Project Structure & Module Organization
- `src/proxy2vpn`: Typer-based CLI and core modules (`cli.py`, `server_manager.py`, `compose_manager.py`, `docker_ops.py`, `models.py`, `config.py`).
- `tests/`: Pytest suite (`test_*.py`) and sample compose file.
- `news/`: Towncrier fragments for changelog entries.
- `scripts/`: Release helpers (e.g., `bump_version.py`).
- Root: `Makefile`, `pyproject.toml`, example compose files (`compose*.yml`), optional local `profiles/*.env` (gitignored).

## Build, Test, and Development Commands
- Setup: `uv sync` or `pip install -e ".[dev]"`.
- Lint: `make lint` (ruff checks).
- Format: `make fmt` / `make fmt-check` (ruff format).
- Test: `make test` (runs `pytest`).
- Changelog: `make changelog-draft` (preview), `make changelog VERSION=x.y.z` (build), `make release VERSION=x.y.z` (bump, changelog, tag).
- Run CLI locally: `uv run proxy2vpn --help`.

## Coding Style & Naming Conventions
- Python 3.10+ with type hints preferred.
- Formatting and imports via ruff; do not hand-tune—run `make fmt`.
- Indentation: 2 spaces, LF line endings (see `.editorconfig`).
- Naming: modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE_CASE`.
- Keep CLI commands coherent with existing verbs (`system`, `profile`, `vpn`, `servers`, `preset`).

## Testing Guidelines
- Framework: `pytest`. Place tests under `tests/` named `test_*.py`.
- Run: `make test` or `uv run --with pytest pytest`.
- Prefer fast, hermetic unit tests; mock Docker and filesystem where practical.
- CI runs ruff and pytest on PRs (see `.github/workflows/`). No strict coverage threshold enforced.

## Commit & Pull Request Guidelines
- Use Conventional Commits (e.g., `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`). Keep messages imperative and scoped.
- PRs should include: clear description, linked issues, before/after notes or screenshots when relevant.
- Add a Towncrier fragment in `news/` (e.g., `123.feature.md`, `123.bugfix.md`). Maintainers build on release.
- All checks must pass before merge.

## Security & Configuration Tips
- Never commit secrets; `*.env` is ignored. Keep credentials in `profiles/*.env` locally.
- Prefer generating compose via `proxy2vpn system init`; avoid committing host‑specific compose changes.
