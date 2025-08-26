"""Module entry point for proxy2vpn CLI."""

from .cli.main import app


def main() -> None:
    """Run the proxy2vpn CLI."""
    raise SystemExit(app())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
