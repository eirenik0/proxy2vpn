import io
from rich.console import Console

from proxy2vpn import fleet_commands


def test_display_allocation_table_empty(monkeypatch):
    buffer = io.StringIO()
    test_console = Console(file=buffer, force_terminal=False)
    monkeypatch.setattr(fleet_commands, "console", test_console)

    fleet_commands._display_allocation_table({})

    output = buffer.getvalue()
    assert "No profiles found in fleet" in output
