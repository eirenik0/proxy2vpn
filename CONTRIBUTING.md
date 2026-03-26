# Contributing

Thank you for considering a contribution to Proxy2VPN!

## Getting Started
- Install dev deps: `uv sync` (recommended) or `pip install -e ".[dev]"`.
- Run the suite: `make fmt-check && make lint && make test`.
- `make test` runs `uv run --with pytest,pytest-xdist pytest -n auto`.

> [!NOTE]
> `uv` is part of the `uv` toolchain. If `uv` isn't installed, get it with:
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```

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
- Keep non-fleet CLI docs aligned with the current model: `vpn add` defines services in compose, `vpn start`/`vpn restart` operate containers, and `vpn update` is the explicit recreate path.
- Keep `fleet` command behavior and documentation stable unless the change explicitly targets fleet.

## Changelog Fragments
We use [Towncrier](https://towncrier.readthedocs.io/) for the changelog.

1. For every PR, create a fragment in `news/` named `<PR_NUMBER>.<type>.md` (e.g., `123.feature.md`).
2. Keep it to a single succinct line.
3. Supported types: `feature`, `bugfix`, `doc`, `removal`, `misc`.
4. Preview with `make changelog-draft`. Maintainers build on release with `make changelog VERSION=x.y.z`.

## Security
- Do not commit secrets or personal `.env` files. Keep credentials in `profiles/*.env` locally.
- Avoid committing host-specific compose changes; generate via `proxy2vpn system init`.
- When using `--compose-file`, remember that generated support files such as `control-server-auth.toml` now live next to that compose file.
