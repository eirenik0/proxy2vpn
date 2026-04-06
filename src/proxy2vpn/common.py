from typing import NoReturn

import typer


def abort(message: str, suggestion: str | None = None, code: int = 1) -> NoReturn:
    """Unified abort for CLI helpers to avoid circular imports."""
    typer.echo(f"Error: {message}", err=True)
    if suggestion:
        typer.echo(f"Hint: {suggestion}", err=True)
    raise typer.Exit(code)
