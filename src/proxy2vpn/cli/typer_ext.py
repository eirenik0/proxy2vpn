from __future__ import annotations

import difflib
import asyncio
import functools
from typing import Any, Callable, Coroutine, TypeVar

from click.exceptions import UsageError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
import typer


T = TypeVar("T")


def run_async(fn: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., T]:
    """Run an async Typer command using ``asyncio.run``.

    The wrapped function must not be executed while another event loop is
    running (e.g. from within Jupyter). In such cases the caller should manage
    the event loop manually instead of using this decorator.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(fn(*args, **kwargs))
        raise RuntimeError(
            "run_async() cannot be used when an event loop is already running"
        )

    return wrapper


class HelpfulTyper(typer.Typer):
    """Typer app with smart error messages and suggestions."""

    def __init__(self, *args, **kwargs) -> None:
        ctx_settings = kwargs.setdefault("context_settings", {})
        ctx_settings.setdefault("help_option_names", ["-h", "--help"])
        super().__init__(*args, **kwargs)
        self.console = Console(stderr=True)

    def __call__(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("standalone_mode", False)
        try:
            return super().__call__(*args, **kwargs)
        except UsageError as exc:
            self._handle_usage_error(exc)
            raise SystemExit(2)
        except FileNotFoundError as exc:
            self.console.print(f"[red]Error:[/red] {exc}")
            raise SystemExit(1)
        except Exception as exc:
            # Handle other exceptions more gracefully
            if "Missing argument" in str(exc) or "required" in str(exc).lower():
                self.console.print("\n[red]Error:[/red] Missing required arguments")
                self.console.print(
                    "[dim]Run the command with '--help' to see required arguments.[/dim]"
                )
                raise SystemExit(2)
            raise

    def _handle_usage_error(self, exc: UsageError) -> None:
        """Handle usage errors with helpful messages."""
        # Handle "Missing command" error for subcommand groups
        if "Missing command" in exc.message:
            self._show_missing_command_help(exc)
        # Handle "No such command" error
        elif "No such command" in exc.message:
            self._show_no_such_command_help(exc)
        else:
            # For other errors, format them nicely
            error_msg = (getattr(exc, "message", "") or str(exc)).strip()

            # Handle missing argument errors
            if "Missing argument" in error_msg:
                self._show_missing_argument_help(exc, error_msg)
            # Handle missing option errors
            elif "Missing option" in error_msg:
                self._show_missing_option_help(exc, error_msg)
            else:
                # Generic error display
                self.console.print(f"[red]Error:[/red] {error_msg}")
                if exc.ctx:
                    self.console.print(
                        f"\nUse '[cyan]{exc.ctx.command_path} --help[/cyan]' for more information."
                    )

    def _show_missing_argument_help(self, exc: UsageError, error_msg: str) -> None:
        """Show help when required arguments are missing."""
        import re

        # Extract the argument name from the error message
        match = re.search(r"'([^']+)'", error_msg)
        arg_name = match.group(1) if match else "argument"

        error_text = Text()
        error_text.append("Missing required argument: ", style="red")
        error_text.append(f"'{arg_name}'", style="yellow")

        self.console.print()
        self.console.print(error_text)

        # Show command usage
        if exc.ctx:
            self.console.print(
                f"\n[yellow]Usage:[/yellow] [cyan]{exc.ctx.command_path} {arg_name.upper()}[/cyan]"
            )

            # Show examples
            examples = self._get_example_commands(exc.ctx.command_path, "")
            if examples and len(examples) > 0:
                self.console.print("\n[blue]Example:[/blue]")
                self.console.print(f"  [dim]$[/dim] [cyan]{examples[0]}[/cyan]")

            self.console.print(
                f"\n[dim]Use '[cyan]{exc.ctx.command_path} --help[/cyan]' for more information.[/dim]"
            )

    def _show_missing_option_help(self, exc: UsageError, error_msg: str) -> None:
        """Show help when required options are missing."""
        import re

        # Extract the option name from the error message
        match = re.search(r"'([^']+)'", error_msg)
        option_name = match.group(1) if match else "option"

        error_text = Text()
        error_text.append("Missing required option: ", style="red")
        error_text.append(f"'{option_name}'", style="yellow")

        self.console.print()
        self.console.print(error_text)

        if exc.ctx:
            self.console.print(
                "\n[yellow]This option is required for this command.[/yellow]"
            )

            # Show example with the option
            self.console.print("\n[blue]Example:[/blue]")
            self.console.print(
                f"  [dim]$[/dim] [cyan]{exc.ctx.command_path} {option_name} <value>[/cyan]"
            )

            self.console.print(
                f"\n[dim]Use '[cyan]{exc.ctx.command_path} --help[/cyan]' for more information.[/dim]"
            )

    def _show_missing_command_help(self, exc: UsageError) -> None:
        """Show help when a subcommand is missing."""
        if not exc.ctx:
            self.console.print("[red]Error:[/red] Missing command.")
            return

        # Create a nice panel with available commands
        command_path = exc.ctx.command_path
        available_commands = exc.ctx.command.list_commands(exc.ctx) if exc.ctx else []

        # Create a table of commands with their help text
        table = Table(
            show_header=True, header_style="bold cyan", box=None, padding=(0, 2)
        )
        table.add_column("Command", style="green")
        table.add_column("Description")

        # Get command descriptions
        for cmd_name in available_commands:
            cmd_obj = self._get_command_object(exc.ctx.command, cmd_name)
            description = self._get_command_help(cmd_obj) if cmd_obj else ""
            table.add_row(cmd_name, description)

        # Create the error panel
        error_text = Text()
        error_text.append("Missing command for ", style="red")
        error_text.append(f"'{command_path}'", style="yellow")

        panel = Panel(
            table,
            title="[red]Error: Missing Command[/red]",
            subtitle=f"[dim]Use '[cyan]{command_path} <command> --help[/cyan]' for details[/dim]",
            border_style="red",
            expand=False,
        )

        self.console.print()
        self.console.print(error_text)
        self.console.print()
        self.console.print(panel)

    def _show_no_such_command_help(self, exc: UsageError) -> None:
        """Show help when a command doesn't exist."""
        import re

        match = re.search(r"'([^']+)'", exc.message)
        bad_cmd = match.group(1) if match else exc.message
        possibilities = exc.ctx.command.list_commands(exc.ctx) if exc.ctx else []

        # Get close matches using different strategies
        matches: list[str] = []

        # 1. Standard fuzzy matching
        fuzzy_matches = difflib.get_close_matches(bad_cmd, possibilities, cutoff=0.4)
        matches.extend(fuzzy_matches)

        # 2. Check for partial matches (prefix matching)
        partial_matches = [
            cmd for cmd in possibilities if cmd.startswith(bad_cmd.lower())
        ]
        for pm in partial_matches:
            if pm not in matches:
                matches.append(pm)

        # 3. Check all command paths (including subcommands)
        all_paths = self._all_command_paths()
        more = difflib.get_close_matches(bad_cmd, all_paths, cutoff=0.4)
        for m in more:
            if m not in matches:
                matches.append(m)

        # 4. Check for common typos (transposed characters, missing letters)
        typo_matches = self._find_typo_matches(bad_cmd, possibilities)
        for tm in typo_matches:
            if tm not in matches:
                matches.append(tm)

        # Create error message
        error_text = Text()
        error_text.append("No such command: ", style="red")
        error_text.append(f"'{bad_cmd}'", style="yellow")

        self.console.print()
        self.console.print(error_text)

        # Show suggestions if available
        if matches:
            self.console.print("\n[green]Did you mean one of these?[/green]")
            for match in matches[:5]:  # Limit to top 5 suggestions
                self.console.print(f"  • [cyan]{match}[/cyan]")

        # Show available commands
        if possibilities:
            self.console.print("\n[yellow]Available commands:[/yellow]")
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Command", style="cyan")
            table.add_column("Description")

            for cmd_name in possibilities:
                cmd_obj = self._get_command_object(exc.ctx.command, cmd_name)
                description = self._get_command_help(cmd_obj) if cmd_obj else ""
                table.add_row(cmd_name, description)

            self.console.print(table)

        # Show example commands if context-appropriate
        if exc.ctx:
            examples = self._get_example_commands(exc.ctx.command_path, bad_cmd)
            if examples:
                self.console.print("\n[blue]Example commands:[/blue]")
                for example in examples:
                    self.console.print(f"  [dim]$[/dim] [cyan]{example}[/cyan]")

        # Show help command
        if exc.ctx:
            self.console.print(
                f"\n[dim]Use '[cyan]{exc.ctx.command_path} --help[/cyan]' for more information.[/dim]"
            )

            # Add documentation link if available
            doc_link = self._get_documentation_link(exc.ctx.command_path)
            if doc_link:
                self.console.print(f"[dim]Documentation: [link]{doc_link}[/link][/dim]")

    def _get_command_object(self, parent_command, cmd_name: str):
        """Get the command object for a given command name."""
        if hasattr(parent_command, "commands"):
            return parent_command.commands.get(cmd_name)
        return None

    def _get_command_help(self, cmd_obj) -> str:
        """Extract help text from a command object."""
        if not cmd_obj:
            return ""

        # Try to get help from various places
        if hasattr(cmd_obj, "help"):
            return cmd_obj.help or ""
        elif hasattr(cmd_obj, "short_help"):
            return cmd_obj.short_help or ""
        elif hasattr(cmd_obj, "__doc__"):
            doc = cmd_obj.__doc__
            if doc:
                # Take first line of docstring
                return doc.strip().split("\n")[0]
        return ""

    def _find_typo_matches(self, bad_cmd: str, possibilities: list[str]) -> list[str]:
        """Find commands that might be typos of the bad command."""
        typo_matches = []
        bad_lower = bad_cmd.lower()

        for cmd in possibilities:
            cmd_lower = cmd.lower()

            # Check for transposed adjacent characters
            if len(bad_lower) == len(cmd_lower) and len(bad_lower) > 1:
                diffs = sum(1 for a, b in zip(bad_lower, cmd_lower) if a != b)
                if diffs <= 2:  # Allow up to 2 character differences
                    typo_matches.append(cmd)
                    continue

            # Check for single missing or extra character
            if abs(len(bad_lower) - len(cmd_lower)) == 1:
                if len(bad_lower) < len(cmd_lower):
                    # Check if bad_cmd is missing one character
                    for i in range(len(cmd_lower)):
                        if cmd_lower[:i] + cmd_lower[i + 1 :] == bad_lower:
                            typo_matches.append(cmd)
                            break
                else:
                    # Check if bad_cmd has one extra character
                    for i in range(len(bad_lower)):
                        if bad_lower[:i] + bad_lower[i + 1 :] == cmd_lower:
                            typo_matches.append(cmd)
                            break

        return typo_matches

    def _get_example_commands(self, command_path: str, bad_cmd: str) -> list[str]:
        """Get example commands based on context."""
        examples = []

        # Context-specific examples based on the command path
        if "vpn" in command_path:
            examples = [
                f"{command_path} create myservice --profile default --port 8888",
                f"{command_path} list",
                f"{command_path} start myservice",
            ]
        elif "profile" in command_path:
            examples = [
                f"{command_path} create myprofile --env-file /path/to/.env",
                f"{command_path} list",
                f"{command_path} delete myprofile",
                f"{command_path} apply myprofile myservice",
            ]
        elif "servers" in command_path:
            examples = [
                f"{command_path} update",
                f"{command_path} list-providers",
                f"{command_path} list-countries nordvpn",
            ]
        elif "system" in command_path:
            examples = [
                f"{command_path} init",
                f"{command_path} validate",
                f"{command_path} diagnose",
            ]
        # Limit to 3 most relevant examples
        return examples[:3]

    def _get_documentation_link(self, command_path: str) -> str | None:
        """Get documentation link for the command."""
        # This could be configured per-project
        # For now, return GitHub repository link
        base_url = "https://github.com/eirenik0/proxy2vpn"

        # Could map specific commands to doc sections
        doc_sections = {
            "vpn": f"{base_url}#vpn-management",
            "profile": f"{base_url}#profile-management",
            "servers": f"{base_url}#server-lists",
            "system": f"{base_url}#system-operations",
        }

        # Find the relevant section based on command path
        for key, url in doc_sections.items():
            if key in command_path.lower():
                return url

        return base_url

    def _all_command_paths(self) -> list[str]:
        """Return all command and subcommand paths for this app."""

        def walk(app: typer.Typer, prefix: str = "") -> list[str]:
            items: list[str] = []
            for cmd in app.registered_commands:
                items.append(f"{prefix}{cmd.name}".strip())
            for grp in app.registered_groups:
                full = f"{prefix}{grp.name}".strip()
                items.append(full)
                items.extend(walk(grp.typer_instance, f"{full} "))
            return items

        return walk(self)
