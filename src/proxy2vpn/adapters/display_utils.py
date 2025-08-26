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


def format_health_score(score) -> str:
    """Format health score with color gradient from red (0) to green (100)."""
    if score == "N/A":
        return "N/A"

    try:
        score = int(score)
    except (ValueError, TypeError):
        return str(score)

    # Clamp score to 0-100 range
    score = max(0, min(100, score))

    # Calculate RGB values for gradient from red to green
    # Red (255, 0, 0) at score 0 -> Green (0, 255, 0) at score 100
    red = int(255 * (100 - score) / 100)
    green = int(255 * score / 100)
    blue = 0

    # Format as Rich RGB color
    return f"[rgb({red},{green},{blue})]{score}[/rgb({red},{green},{blue})]"
