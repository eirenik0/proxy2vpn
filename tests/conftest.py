import pytest
from proxy2vpn import compose_validator


class _AlwaysValidServerManager:
    def update_servers(self):
        self.data = {}
        return self.data

    def validate_location(self, provider, location):
        return True


@pytest.fixture(autouse=True)
def _patch_server_manager(monkeypatch):
    monkeypatch.setattr(
        compose_validator, "ServerManager", lambda: _AlwaysValidServerManager()
    )
