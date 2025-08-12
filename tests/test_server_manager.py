import pytest
import requests
import typer

from proxy2vpn.server_manager import ServerManager


def test_update_servers_ssl_error(tmp_path, monkeypatch):
    mgr = ServerManager(cache_dir=tmp_path)

    def fake_get(*args, **kwargs):
        raise requests.exceptions.SSLError("bad ssl")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(typer.Exit) as excinfo:
        mgr.update_servers()
    assert excinfo.value.exit_code == 1


def test_update_servers_insecure_flag(tmp_path, monkeypatch):
    called = {}

    class Resp:
        text = "{}"
        headers = {"content-length": "2"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size: int):
            yield b"{}"

    def fake_get(url, timeout, verify, stream=False):
        called["verify"] = verify
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)

    mgr = ServerManager(cache_dir=tmp_path)
    mgr.update_servers(verify=False)
    assert called["verify"] is False


def test_location_helpers():
    mgr = ServerManager()
    mgr.data = {
        "prov": {
            "servers": [
                {"country": "US", "city": "New York"},
                {"country": "US", "city": "Los Angeles"},
                {"country": "CA", "city": "Toronto"},
            ]
        }
    }
    assert mgr.list_countries("prov") == ["CA", "US"]
    assert mgr.list_cities("prov", "US") == ["Los Angeles", "New York"]
    assert mgr.validate_location("prov", "Toronto")
    assert not mgr.validate_location("prov", "Paris")
