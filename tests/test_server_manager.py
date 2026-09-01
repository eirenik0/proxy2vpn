import pytest
import typer
from proxy2vpn import server_manager
from proxy2vpn.adapters.http_client import HTTPClientError


def test_update_servers_ssl_error(tmp_path, monkeypatch):
    mgr = server_manager.ServerManager(cache_dir=tmp_path)

    class DummyClient:
        def __init__(self, cfg):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def get(self, path):
            raise HTTPClientError("bad ssl")

    monkeypatch.setattr(server_manager, "HTTPClient", lambda cfg: DummyClient(cfg))

    with pytest.raises(typer.Exit) as excinfo:
        mgr.update_servers()
    assert excinfo.value.exit_code == 1


def test_update_servers_insecure_flag(tmp_path, monkeypatch):
    called = {}

    class DummyClient:
        def __init__(self, cfg):
            called["verify"] = cfg.verify_ssl

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def get(self, path):
            return {}

    monkeypatch.setattr(server_manager, "HTTPClient", DummyClient)

    mgr = server_manager.ServerManager(cache_dir=tmp_path)
    mgr.update_servers(verify=False)
    assert called["verify"] is False


def test_update_servers_assembles_gluetun_manifest(tmp_path, monkeypatch):
    requested = []

    class DummyClient:
        def __init__(self, cfg):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def get(self, path):
            requested.append(path)
            if path.endswith("manifest.json"):
                return {
                    "private internet access": {
                        "filepath": "/gluetun/servers/private internet access.json"
                    },
                    "protonvpn": {"filepath": "/gluetun/servers/protonvpn.json"},
                    "version": 1,
                }
            if path.endswith("private%20internet%20access.json"):
                return {"version": 3, "servers": [{"country": "US"}]}
            return {"version": 4, "servers": [{"country": "SK"}]}

    monkeypatch.setattr(server_manager, "HTTPClient", DummyClient)

    mgr = server_manager.ServerManager(cache_dir=tmp_path)
    data = mgr.update_servers()

    assert data == {
        "version": 1,
        "private internet access": {
            "version": 3,
            "servers": [{"country": "US"}],
        },
        "protonvpn": {"version": 4, "servers": [{"country": "SK"}]},
    }
    assert requested == [
        "/qdm12/gluetun-servers/main/pkg/servers/manifest.json",
        "/qdm12/gluetun-servers/main/pkg/servers/private%20internet%20access.json",
        "/qdm12/gluetun-servers/main/pkg/servers/protonvpn.json",
    ]


def test_location_helpers():
    mgr = server_manager.ServerManager()
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
    assert mgr.validate_location("prov", "CA")
    assert mgr.validate_location("prov", "Toronto,CA")
    assert not mgr.validate_location("prov", "Paris")
    assert not mgr.validate_location("prov", "Paris,FR")
    assert mgr.parse_location("prov", "Toronto") == ("Toronto", None)
    assert mgr.parse_location("prov", "CA") == (None, "CA")
    assert mgr.parse_location("prov", "Toronto,CA") == ("Toronto", "CA")
