import asyncio
import json

import aiohttp
from aiohttp import web
import pytest
from typer.testing import CliRunner

from proxy2vpn.adapters import http_client
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.gluetun_runtime import GluetunRuntime
from proxy2vpn.adapters.proxy_utils import build_proxy_urls_from_container
from proxy2vpn.cli.main import app
from proxy2vpn.core import config, security
from proxy2vpn.core.models import VPNService


def service(**kwargs):
    return VPNService.create(
        name="vpn",
        port=20000,
        control_port=30000,
        provider="test",
        profile="test",
        location="",
        environment={},
        labels={},
        **kwargs,
    )


# @lat: [[lat.md/security-tests#Deployment Security Tests#Generated Credentials]]
def test_generated_credentials_are_private_separate_and_stable(tmp_path):
    compose = tmp_path / "state" / "compose.yml"
    assert security.ensure_control_auth(compose)
    monitor = security.load_control_auth(compose, "monitor")
    operator = security.load_control_auth(compose, "operator")
    assert monitor is not None and operator is not None
    assert monitor[1] != operator[1] and len(monitor[1]) >= 32
    auth_text = config.resolve_control_auth_config(compose).read_text()
    monitor_role, operator_role = auth_text.split("[[roles]]")[1:]
    assert '"PUT ' not in monitor_role
    assert '"GET ' not in operator_role
    assert "/settings" not in auth_text
    for filename in ("control-server-auth.toml", security.CLIENT_AUTH_FILE):
        assert (compose.parent / filename).stat().st_mode & 0o777 == 0o600
    assert not security.ensure_control_auth(compose)
    assert not security.ensure_control_auth(compose, replace=True)
    assert security.load_control_auth(compose, "monitor") == monitor


# @lat: [[lat.md/security-tests#Deployment Security Tests#Legacy And Custom Authentication]]
def test_custom_auth_is_preserved_and_explicit_migration_has_private_backup(tmp_path):
    compose = tmp_path / "compose.yml"
    auth = config.resolve_control_auth_config(compose)
    legacy = '[[roles]]\nname = "legacy"\nauth = "none"\n'
    auth.write_text(legacy)
    assert not security.ensure_control_auth(compose)
    assert auth.read_text() == legacy
    assert security.load_control_auth(compose, "monitor") is None
    assert security.ensure_control_auth(compose, replace=True)
    backup = auth.with_name(auth.name + ".bak")
    assert backup.read_text() == legacy
    assert backup.stat().st_mode & 0o777 == 0o600
    assert 'auth = "none"' not in auth.read_text()


# @lat: [[lat.md/security-tests#Deployment Security Tests#Invalid Credential Files]]
@pytest.mark.parametrize(
    "failure", ["missing", "invalid", "public", "public-server", "mismatch", "symlink"]
)
def test_invalid_generated_credentials_fail_without_secret_values(tmp_path, failure):
    compose = tmp_path / "compose.yml"
    security.ensure_control_auth(compose)
    credentials = tmp_path / security.CLIENT_AUTH_FILE
    token = json.loads(credentials.read_text())["monitor"]["password"]
    if failure == "missing":
        credentials.unlink()
    elif failure == "invalid":
        credentials.write_text(token)
    elif failure == "public":
        credentials.chmod(0o644)
    elif failure == "public-server":
        config.resolve_control_auth_config(compose).chmod(0o644)
    elif failure == "mismatch":
        payload = json.loads(credentials.read_text())
        payload["monitor"]["password"] = "wrong-password"
        credentials.write_text(json.dumps(payload))
    else:
        target = tmp_path / "target.json"
        credentials.rename(target)
        credentials.symlink_to(target)
    with pytest.raises(ValueError) as error:
        security.load_control_auth(compose, "monitor")
    assert token not in str(error.value)
    if failure != "symlink":
        assert security.ensure_control_auth(compose, replace=True)
        assert security.load_control_auth(compose, "monitor") is not None


# @lat: [[lat.md/security-tests#Deployment Security Tests#Credential Write Rollback]]
def test_credential_write_failure_restores_both_files(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    server = config.resolve_control_auth_config(compose)
    server.write_text("custom auth")
    write = security._private_write

    def fail_server(path, content):
        if path == server and content.startswith(security.MANAGED_AUTH_HEADER):
            raise OSError("injected failure")
        write(path, content)

    monkeypatch.setattr(security, "_private_write", fail_server)
    with pytest.raises(OSError):
        security.ensure_control_auth(compose, replace=True)
    assert server.read_text() == "custom auth"
    assert not (tmp_path / security.CLIENT_AUTH_FILE).exists()


# @lat: [[lat.md/security-tests#Deployment Security Tests#Authenticated Control Requests]]
def test_control_requests_use_distinct_roles_and_deny_wrong_role(tmp_path, monkeypatch):
    monkeypatch.delenv("GLUETUN_CONTROL_AUTH", raising=False)
    compose = tmp_path / "compose.yml"
    security.ensure_control_auth(compose)
    monitor = security.load_control_auth(compose, "monitor")
    operator = security.load_control_auth(compose, "operator")
    seen = []

    async def run():
        async def handler(request):
            auth = aiohttp.BasicAuth.decode(request.headers.get("Authorization", ""))
            pair = (auth.login, auth.password)
            seen.append((request.method, pair))
            expected = monitor if request.method == "GET" else operator
            if pair != expected:
                raise web.HTTPForbidden()
            return web.json_response({"status": "running"})

        server = web.Application()
        server.router.add_route("*", "/v1/openvpn/status", handler)
        runner = web.AppRunner(server)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        try:
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}/v1"
            async with http_client.GluetunControlClient(
                url, compose_file=compose
            ) as client:
                await client.status()
                await client.request(
                    "PUT", "/v1/openvpn/status", json={"status": "running"}
                )
            async with aiohttp.ClientSession(
                auth=aiohttp.BasicAuth(*monitor)
            ) as client:
                async with client.put(url + "/openvpn/status") as response:
                    assert response.status == 403
        finally:
            await runner.cleanup()

    asyncio.run(run())
    assert seen[:2] == [("GET", monitor), ("PUT", operator)]


# @lat: [[lat.md/security-tests#Deployment Security Tests#Compose Scoped Clients]]
def test_control_credentials_follow_compose_root_and_env_override(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GLUETUN_CONTROL_AUTH", raising=False)
    first = tmp_path / "first" / "compose.yml"
    second = tmp_path / "second" / "compose.yml"
    security.ensure_control_auth(first)
    security.ensure_control_auth(second)
    one = http_client.GluetunControlClient("http://localhost:30000", compose_file=first)
    two = http_client.GluetunControlClient(
        "http://localhost:30000", compose_file=second
    )
    assert one._config.auth != two._config.auth
    remote = http_client.GluetunControlClient("https://example.com", compose_file=first)
    assert remote._config.auth is None
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "custom:password")
    override = http_client.GluetunControlClient(
        "http://localhost:30000", compose_file=first
    )
    assert override._config.auth == ("custom", "password")


# @lat: [[lat.md/security-tests#Deployment Security Tests#Runtime Compose Ownership]]
def test_runtime_control_clients_receive_owned_compose_path(tmp_path):
    owned = tmp_path / "state" / "compose.yml"
    vpn = service()
    vpn.labels[config.COMPOSE_FILE_LABEL] = str(owned)
    seen = []

    class Client:
        def __init__(self, url, **kwargs):
            seen.append(kwargs["compose_file"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def status(self):
            pass

    outcome = asyncio.run(
        GluetunRuntime(control_client_factory=Client).control_status(vpn)
    )
    assert outcome.success and seen == [owned]


# @lat: [[lat.md/security-tests#Deployment Security Tests#Proxy Binding Compatibility]]
@pytest.mark.parametrize(
    "ports,expected",
    [
        (["20000:8888/tcp"], "0.0.0.0"),
        (["0.0.0.0:20000:8888/tcp"], "0.0.0.0"),
        (["192.0.2.10:20000:8888/tcp"], "192.0.2.10"),
        (["[::1]:20000:8888/tcp"], "::1"),
        ([{"target": 8888, "published": 20000, "host_ip": "192.0.2.10"}], "192.0.2.10"),
    ],
)
def test_existing_proxy_bindings_roundtrip(ports, expected):
    vpn = VPNService.from_compose_service(
        "vpn",
        {
            "ports": ports + ["127.0.0.1:30000:8000/tcp"],
            "labels": {},
        },
    )
    assert vpn.proxy_bind_address == expected
    restored = VPNService.from_compose_service("vpn", vpn.to_compose_service())
    assert restored.proxy_bind_address == expected
    assert service().proxy_bind_address == "127.0.0.1"


# @lat: [[lat.md/security-tests#Deployment Security Tests#Explicit Migration]]
def test_secure_migration_preserves_other_fields_and_does_not_reset_compose(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("""services:
  vpn:
    image: qmcgaw/gluetun
    restart: unless-stopped
    ports:
      - "0.0.0.0:20000:8888/tcp"
      - "127.0.0.1:30000:8000/tcp"
      - "127.0.0.1:9000:9000/tcp"
    labels:
      vpn.type: vpn
      vpn.port: "20000"
      vpn.control_port: "30000"
""")
    legacy = '[[roles]]\nname = "legacy"\nauth = "none"\n'
    config.resolve_control_auth_config(compose).write_text(legacy)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose),
            "system",
            "secure",
            "--replace-control-auth",
        ],
    )
    assert result.exit_code == 0, result.output
    data = ComposeManager(compose).data
    definition = data["services"]["vpn"]
    assert definition["restart"] == "unless-stopped"
    assert definition["ports"] == [
        "127.0.0.1:20000:8888/tcp",
        "127.0.0.1:30000:8000/tcp",
        "127.0.0.1:9000:9000/tcp",
    ]
    assert "live containers were not changed" in result.output
    assert "0.0.0.0:20000" in compose.with_suffix(".yml.bak").read_text()
    generated = (tmp_path / security.CLIENT_AUTH_FILE).read_text()
    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose),
            "system",
            "secure",
            "--replace-control-auth",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "0.0.0.0:20000" in compose.with_suffix(".yml.bak").read_text()
    assert (tmp_path / security.CLIENT_AUTH_FILE).read_text() == generated
    result = runner.invoke(
        app,
        [
            "--compose-file",
            str(compose),
            "system",
            "init",
            "--force",
            "--skip-server-refresh",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / security.CLIENT_AUTH_FILE).read_text() == generated


# @lat: [[lat.md/security-tests#Deployment Security Tests#Probe Bind Addresses]]
@pytest.mark.parametrize(
    "bind,expected",
    [
        ("192.0.2.10", "192.0.2.10"),
        ("::1", "[::1]"),
        ("0.0.0.0", "127.0.0.1"),
        ("::", "[::1]"),
    ],
)
def test_proxy_probe_uses_published_address(bind, expected):
    class Container:
        attrs = {
            "Config": {"Env": ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=password"]},
            "NetworkSettings": {"Ports": {"8888/tcp": [{"HostIp": bind}]}},
        }

    url = build_proxy_urls_from_container(Container(), 20000)["https"]
    assert url == f"http://user:password@{expected}:20000"


# @lat: [[lat.md/security-tests#Deployment Security Tests#Proxy Export Exposure]]
@pytest.mark.parametrize("direct_ip", ["203.0.113.10", ""])
def test_export_uses_local_binding_instead_of_public_host(monkeypatch, direct_ip):
    from proxy2vpn.adapters import docker_ops

    class Container:
        status = "running"
        labels = {"vpn.port": "20000"}
        attrs = {
            "Config": {"Env": []},
            "NetworkSettings": {"Ports": {"8888/tcp": [{"HostIp": "127.0.0.1"}]}},
        }

    async def public_ip():
        return direct_ip

    monkeypatch.setattr(docker_ops, "get_vpn_containers", lambda all: [Container()])
    monkeypatch.setattr(docker_ops.ip_utils, "fetch_ip_async", public_ip)
    records = asyncio.run(docker_ops.collect_proxy_info(include_credentials=False))
    assert records[0]["host"] == "127.0.0.1"


# @lat: [[lat.md/security-tests#Deployment Security Tests#Diagnostic Bind Addresses]]
def test_log_diagnostic_probes_private_binding(monkeypatch):
    from types import SimpleNamespace
    from proxy2vpn.adapters import docker_ops

    container = SimpleNamespace(
        labels={"vpn.port": "20000"},
        attrs={
            "Config": {"Env": []},
            "NetworkSettings": {"Ports": {"8888/tcp": [{"HostIp": "192.168.1.10"}]}},
        },
    )
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    monkeypatch.setattr(docker_ops, "_client", lambda: client)
    monkeypatch.setattr(docker_ops, "container_logs", lambda *args, **kwargs: [])
    calls = []

    def fetch_ip(*, proxies=None, timeout=5):
        calls.append(proxies)
        return "198.51.100.1"

    monkeypatch.setattr(docker_ops.ip_utils, "fetch_ip", fetch_ip)
    results = docker_ops.analyze_container_logs("vpn-test", direct_ip="203.0.113.1")
    assert calls[0]["http"] == "http://192.168.1.10:20000"
    assert any(result.check == "connectivity" and result.passed for result in results)
