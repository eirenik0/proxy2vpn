from __future__ import annotations
from typing import NoReturn

import typer


def abort(message: str, suggestion: str | None = None, code: int = 1) -> NoReturn:
    """Print a standardized error message and exit."""
    typer.echo(f"Error: {message}", err=True)
    if suggestion:
        typer.echo(f"Hint: {suggestion}", err=True)
    raise typer.Exit(code)
