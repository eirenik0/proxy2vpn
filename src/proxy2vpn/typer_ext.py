from __future__ import annotations

import difflib
from typing import List

from click.exceptions import UsageError
import typer


class HelpfulTyper(typer.Typer):
    """Typer app with smart error messages and suggestions."""

    def __init__(self, *args, **kwargs) -> None:
        ctx_settings = kwargs.setdefault("context_settings", {})
        ctx_settings.setdefault("help_option_names", ["-h", "--help"])
        super().__init__(*args, **kwargs)

    def __call__(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("standalone_mode", False)
        try:
            return super().__call__(*args, **kwargs)
        except UsageError as exc:
            if "No such command" not in exc.message:
                raise
            import re

            match = re.search(r"'([^']+)'", exc.message)
            bad_cmd = match.group(1) if match else exc.message
            possibilities = exc.ctx.command.list_commands(exc.ctx) if exc.ctx else []
            matches: List[str] = difflib.get_close_matches(bad_cmd, possibilities, cutoff=0.4)
            more = difflib.get_close_matches(bad_cmd, self._all_command_paths(), cutoff=0.4)
            for m in more:
                if m not in matches:
                    matches.append(m)
            message = f"No such command '{bad_cmd}'."
            if matches:
                message += "\n\nDid you mean:\n    " + "\n    ".join(matches)
            if possibilities:
                message += "\n\nAvailable commands:\n    " + "\n    ".join(possibilities)
            message += f"\n\nUse '{exc.ctx.command_path} --help' for more information."
            typer.echo(message, err=True)
            raise SystemExit(2)

    def _all_command_paths(self) -> List[str]:
        """Return all command and subcommand paths for this app."""

        def walk(app: typer.Typer, prefix: str = "") -> List[str]:
            items: List[str] = []
            for cmd in app.registered_commands:
                items.append(f"{prefix}{cmd.name}".strip())
            for grp in app.registered_groups:
                full = f"{prefix}{grp.name}".strip()
                items.append(full)
                items.extend(walk(grp.typer_instance, f"{full} "))
            return items

        return walk(self)
