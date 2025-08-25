"""Shared console and display utilities."""

from rich.console import Console
from rich.table import Table

console = Console()


def format_success_message(operation: str, name: str) -> str:
    """Format a standardized success message."""
    return f"[green]✓[/green] {operation} '{name}'"


def format_bulk_success_message(operation: str, name: str) -> str:
    """Format a standardized success message for bulk operations."""
    return f"[green]✓[/green] {operation} {name}"


def display_operation_results(
    succeeded: list[str], failed: list[str], operation: str
) -> None:
    """Display results of bulk operations with success/failure counts."""
    if succeeded:
        for name in succeeded:
            console.print(format_bulk_success_message(operation, name))

    if failed:
        console.print(f"[red]Failed to {operation.lower()}:[/red]")
        for name in failed:
            console.print(f"  - {name}")


def create_service_table(title: str, include_health: bool = False) -> Table:
    """Create a standardized service listing table."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Service", style="cyan", no_wrap=True)
    table.add_column("Profile", style="green")
    table.add_column("Provider", style="yellow")
    table.add_column("Location", style="blue")
    table.add_column("Port", style="red")
    table.add_column("Status", style="bold")

    if include_health:
        table.add_column("Health", style="bold")

    return table
