# Contributing

Thank you for considering a contribution to Proxy2VPN!

## Getting Started
- Install dev deps: `uv sync` or `pip install -e ".[dev]"`.
- Run the suite: `make fmt-check && make lint && make test`.

## Code Style
- Python 3.10+ with type hints encouraged.
- Use ruff for formatting and linting: `make fmt`, `make lint`.
- Follow naming: modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE_CASE`.

## Tests
- Place tests in `tests/` as `test_*.py` using `pytest`.
- Prefer small, deterministic tests; mock Docker/filesystem where feasible.

## Commit Messages
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, etc.
- Keep messages imperative and scoped (e.g., `feat: add version option`).

## Pull Requests
- Fill out the PR template; include description, linked issues, and test steps.
- Ensure `make fmt-check`, `make lint`, and `make test` pass.
- Add or update tests for behavior changes.

## Changelog Fragments
We use [Towncrier](https://towncrier.readthedocs.io/) for the changelog.

1. For every PR, create a fragment in `news/` named `<PR_NUMBER>.<type>.md` (e.g., `123.feature.md`).
2. Keep it to a single succinct line.
3. Supported types: `feature`, `bugfix`, `doc`, `removal`, `misc`.
4. Preview with `make changelog-draft`. Maintainers build on release with `make changelog VERSION=x.y.z`.

## Security
- Do not commit secrets or personal `.env` files. Keep credentials in `profiles/*.env` locally.
- Avoid committing host-specific compose changes; generate via `proxy2vpn system init`.
