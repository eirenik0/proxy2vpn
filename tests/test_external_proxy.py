"""Exercise external endpoints against a real, controlled HTTP CONNECT proxy."""

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import socket
import ssl
import subprocess
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from proxy2vpn.adapters.egress import GluetunEgressAdapter
from proxy2vpn.adapters.external_proxy import (
    ExternalProxyAdapter,
    load_external_endpoints,
)
from proxy2vpn.adapters.gluetun_runtime import RuntimeInspection
from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import AgentState
from proxy2vpn.agent.recovery_policy import (
    RecoveryContext,
    RecoveryPolicy,
    RecoverySettings,
    ServiceIdentity,
)
from proxy2vpn.agent.runtime import AgentWatchdog
from proxy2vpn.cli.main import app
from proxy2vpn.core.egress import (
    EgressAdapter,
    EgressCapabilities,
    UnsupportedEgressOperation,
)
from proxy2vpn.core.external_proxy import ExternalProxyEndpoint
from proxy2vpn.core.models import VPNService
from proxy2vpn.core.services.diagnostics import DiagnosticResult
from proxy2vpn.core.services.health_assessment import HealthAssessmentService


def endpoint(port=3128, **overrides):
    return ExternalProxyEndpoint.model_validate(
        {
            "id": "office-proxy",
            "connection": {"host": "127.0.0.1", "port": port},
            "probe_urls": ["https://probe.invalid/ip"],
            **overrides,
        }
    )


def write_config(root, endpoints):
    path = root / "external-proxies.json"
    path.write_text(
        json.dumps(
            {"version": 1, "endpoints": [item.model_dump() for item in endpoints]}
        )
    )
    return path


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    root = tmp_path_factory.mktemp("connect-tls")
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=probe.invalid",
            "-addext",
            "subjectAltName=DNS:probe.invalid,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    return server_context, client_context


@asynccontextmanager
async def controlled_proxy(
    tls_material,
    *,
    status=200,
    body=b"198.51.100.8",
    credentials=None,
    upstream_status=200,
):
    server_context, client_context = tls_material
    state = {"connects": [], "requests": [], "proxy_auth": []}
    writers = set()
    tasks = set()

    async def origin(reader, writer):
        writers.add(writer)
        try:
            state["requests"].append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                f"HTTP/1.1 {upstream_status} OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    target = await asyncio.start_server(origin, "127.0.0.1", 0, ssl=server_context)
    target_port = target.sockets[0].getsockname()[1]

    async def pump(reader, writer):
        while data := await reader.read(4096):
            writer.write(data)
            await writer.drain()

    async def proxy(reader, writer):
        tasks.add(asyncio.current_task())
        writers.add(writer)
        upstream = None
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            state["connects"].append(header.split(b"\r\n")[0].decode())
            auth = next(
                (
                    line.split(b": ", 1)[1]
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"proxy-authorization:")
                ),
                None,
            )
            state["proxy_auth"].append(auth)
            response_status = status
            if credentials and auth != b"Basic " + base64.b64encode(
                credentials.encode()
            ):
                response_status = 407
            if response_status != 200:
                writer.write(
                    f"HTTP/1.1 {response_status} Rejected\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
                return
            upstream_reader, upstream = await asyncio.open_connection(
                "127.0.0.1", target_port
            )
            writers.add(upstream)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            forwarding = [
                asyncio.create_task(pump(reader, upstream)),
                asyncio.create_task(pump(upstream_reader, writer)),
            ]
            try:
                await asyncio.wait(forwarding, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in forwarding:
                    task.cancel()
                await asyncio.gather(*forwarding, return_exceptions=True)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            for connection in (writer, upstream):
                if connection is not None:
                    connection.close()
                    await connection.wait_closed()
                    writers.discard(connection)
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    proxy_port = server.sockets[0].getsockname()[1]
    try:
        yield endpoint(proxy_port), client_context, state, target_port
    finally:
        server.close()
        target.close()
        await server.wait_closed()
        await target.wait_closed()
        for writer in list(writers):
            writer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)


# @lat: [[lat.md/egress-tests#External Egress Tests#CONNECT DNS And Authentication]]
def test_real_connect_uses_proxy_dns_and_keeps_auth_out_of_target(
    tls_material, monkeypatch
):
    monkeypatch.setenv("PROXY_USERNAME", "operator")
    monkeypatch.setenv("PROXY_PASSWORD", "secret-value")
    original_resolve = socket.getaddrinfo
    looked_up = []

    def resolve(host, *args, **kwargs):
        looked_up.append(host)
        assert host != "probe.invalid"
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setenv("HTTPS_PROXY", "http://unusable.invalid:1")

    async def exercise():
        async with controlled_proxy(
            tls_material, credentials="operator:secret-value"
        ) as (item, tls, state, _):
            item = item.model_copy(
                update={
                    "credentials": endpoint(
                        credentials={
                            "username_env": "PROXY_USERNAME",
                            "password_env": "PROXY_PASSWORD",
                        }
                    ).credentials
                }
            )
            adapter: EgressAdapter = ExternalProxyAdapter(item, tls_context=tls)
            result = await adapter.observe()
            assert result.health_score == 100
            assert result.authentication is True and result.connectivity is True
            assert result.current_egress_ip == "198.51.100.8"
            assert result.latency_ms is not None and result.latency_ms >= 0
            assert state["connects"] == ["CONNECT probe.invalid:443 HTTP/1.1"]
            assert b"Proxy-Authorization" not in state["requests"][0]
            assert b"secret-value" not in state["requests"][0]
            assert adapter.identity == item.identity
            assert not adapter.capabilities.request_different_exit_ip

    asyncio.run(exercise())
    assert "probe.invalid" not in looked_up


# @lat: [[lat.md/egress-tests#External Egress Tests#Authentication Failure And Redaction]]
def test_proxy_authentication_failure_redacts_all_diagnostics(
    tls_material, monkeypatch, tmp_path, isolated_logging
):
    from proxy2vpn.adapters.logging_utils import configure_logging

    log_file = tmp_path / "probe.log"
    configure_logging(log_file=log_file)
    monkeypatch.setenv("PROXY_USERNAME", "private-user")
    monkeypatch.setenv("PROXY_PASSWORD", "private-pass")

    async def exercise():
        async with controlled_proxy(tls_material, status=407) as (item, tls, state, _):
            item = item.model_copy(
                update={
                    "credentials": endpoint(
                        credentials={
                            "username_env": "PROXY_USERNAME",
                            "password_env": "PROXY_PASSWORD",
                        }
                    ).credentials
                }
            )
            result = await ExternalProxyAdapter(item, tls_context=tls).observe()
            assert result.health_class == "auth_config"
            assert result.authentication is False and result.connectivity is False
            assert result.current_egress_ip is None
            assert not state["requests"]
            text = repr(result) + item.model_dump_json()
            assert "private-user" not in text and "private-pass" not in text
            # Exercise the complete public assessment/logging path as well.
            assessment = await HealthAssessmentService().assess_service(item)
            assert assessment.health_class == "auth_config"

    asyncio.run(exercise())
    assert "private-user" not in log_file.read_text()
    assert "private-pass" not in log_file.read_text()


# @lat: [[lat.md/egress-tests#External Egress Tests#No Direct Fallback]]
@pytest.mark.parametrize(
    "failure", ["unreachable_proxy", "unreachable_upstream", "tls_failure"]
)
def test_failures_never_contact_target_directly(tls_material, failure):
    async def exercise():
        async with controlled_proxy(
            tls_material, status=502 if failure == "unreachable_upstream" else 200
        ) as (item, tls, state, target_port):
            if failure == "unreachable_proxy":
                unused = await asyncio.start_server(
                    lambda r, w: w.close(), "127.0.0.1", 0
                )
                closed_port = unused.sockets[0].getsockname()[1]
                unused.close()
                await unused.wait_closed()
                item = endpoint(
                    closed_port, probe_urls=[f"https://127.0.0.1:{target_port}/ip"]
                )
            adapter = ExternalProxyAdapter(
                item,
                probe_timeout=1,
                tls_context=None if failure == "tls_failure" else tls,
            )
            result = await adapter.observe()
            assert result.health_score == 0 and result.current_egress_ip is None
            assert result.connectivity is False
            assert not state["requests"]
            if failure == "unreachable_proxy":
                assert not state["connects"]
            else:
                assert len(state["connects"]) == 1

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Unavailable Egress Evidence]]
@pytest.mark.parametrize(
    "body", [b"", b"not an IP", b"password=private-pass 198.51.100.8", b"a" * 5000]
)
def test_completed_request_without_ip_is_unknown(tls_material, body):
    async def exercise():
        async with controlled_proxy(tls_material, body=body) as (item, tls, _, _):
            result = await ExternalProxyAdapter(item, tls_context=tls).observe()
            assert result.health_score == 0 and result.health_class == "unknown"
            assert result.connectivity is True and result.authentication is None
            assert result.current_egress_ip is None
            assert (
                next(r for r in result.results if r.check == "egress_ip").passed is None
            )
            assert "private-pass" not in repr(result)

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Source Appropriate IP Rules]]
@pytest.mark.parametrize(
    "expected,score", [([], 100), (["198.51.100.8"], 100), (["203.0.113.8"], 0)]
)
def test_allowlist_rules_do_not_depend_on_host_ip(tls_material, expected, score):
    async def exercise():
        async with controlled_proxy(tls_material) as (item, tls, _, _):
            result = await ExternalProxyAdapter(
                item.model_copy(update={"expected_egress_ips": expected}),
                tls_context=tls,
            ).observe()
            assert result.health_score == score
            assert result.current_egress_ip == "198.51.100.8"
            assert result.connectivity is True

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Unsupported Operations]]
@pytest.mark.parametrize(
    "operation",
    [
        "restart_tunnel",
        "restore",
        "replace_endpoint",
        "replace_session",
        "request_different_exit_ip",
    ],
)
def test_unsupported_operations_raise_without_effects(operation, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Unsupported operations must not execute I/O")

    async def exercise():
        monkeypatch.setattr(socket, "socket", unexpected)
        with pytest.raises(UnsupportedEgressOperation, match=operation):
            await ExternalProxyAdapter(endpoint()).execute(operation)
        monkeypatch.undo()

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Configuration And Credential References]]
def test_configuration_validation_and_safe_errors(tmp_path, monkeypatch):
    item = endpoint(
        credentials={"username_env": "PROXY_USERNAME", "password_env": "PROXY_PASSWORD"}
    )
    path = write_config(tmp_path, [item])
    assert load_external_endpoints(path) == [item]
    assert load_external_endpoints(tmp_path / "absent.json") == []
    monkeypatch.delenv("PROXY_USERNAME", raising=False)
    monkeypatch.delenv("PROXY_PASSWORD", raising=False)
    result = asyncio.run(ExternalProxyAdapter(item).observe())
    assert result.health_class == "auth_config" and result.authentication is None
    write_config(tmp_path, [item, item])
    with pytest.raises(ValueError, match="unique"):
        load_external_endpoints(path)
    for changes in [
        {
            "connection": {
                "host": "http://private-user:private-pass@localhost",
                "port": 80,
            }
        },
        {"connection": {"host": "localhost", "port": 0}},
        {"connection": {"host": "localhost", "port": 80, "protocol": "socks5"}},
        {"probe_urls": ["http://probe.invalid/ip"]},
        {"probe_urls": ["https://private-user:private-pass@probe.invalid/ip"]},
        {"credentials": {"username": "private-user", "password": "private-pass"}},
        {"expected_egress_ips": ["invalid"]},
    ]:
        path.write_text(
            json.dumps({"version": 1, "endpoints": [{**item.model_dump(), **changes}]})
        )
        with pytest.raises(ValueError) as error:
            load_external_endpoints(path)
        assert "private-user" not in str(error.value) and "private-pass" not in str(
            error.value
        )


# @lat: [[lat.md/egress-tests#External Egress Tests#Both Adapters Share Health Interface]]
def test_shared_interface_and_mixed_assessments(fake_gluetun_runtime, monkeypatch):
    vpn = VPNService.create("vpn", 8080, 30000, "provider", "account", "city", {}, {})
    fake_gluetun_runtime.inspect.return_value = RuntimeInspection(
        "running",
        [
            DiagnosticResult(
                check="connectivity", passed=True, message="working", recommendation=""
            )
        ],
        control_api_reachable=True,
    )
    external = endpoint()

    async def observe(self, **kwargs):
        from proxy2vpn.core.egress import EgressObservation

        return EgressObservation(
            100,
            "healthy",
            available=True,
            connectivity=True,
            current_egress_ip="198.51.100.8",
        )

    monkeypatch.setattr(ExternalProxyAdapter, "observe", observe)

    async def exercise():
        adapters: list[EgressAdapter] = [
            GluetunEgressAdapter(vpn, fake_gluetun_runtime),
            ExternalProxyAdapter(external),
        ]
        for adapter in adapters:
            assert (await adapter.observe()).health_class == "healthy"
        assessed = await HealthAssessmentService(
            runtime=fake_gluetun_runtime
        ).assess_services([vpn, external])
        assert assessed["vpn"].container_status == "running"
        assert assessed["vpn"].capabilities.restart_tunnel
        assert assessed[external.name].container_status == "not_applicable"
        assert assessed[external.name].control_api_reachable is None
        assert assessed[external.name].direct_ip is None
        assert assessed[external.name].health_class == "healthy"
        assert assessed[external.name].peer_evidence.healthy == []
        fake_gluetun_runtime.inspect.assert_awaited()
        assert all(
            call.args[0] is vpn for call in fake_gluetun_runtime.inspect.call_args_list
        )

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Capability Driven Recovery]]
def test_policy_uses_capabilities_without_backend_details(monkeypatch):
    from proxy2vpn.core.services.health_assessment import HealthAssessment

    now = datetime.now(timezone.utc)
    policy = RecoveryPolicy(RecoverySettings(60, 15, 300, 600, 1800))
    assessment = HealthAssessment(
        service_name="office-proxy",
        assessed_at=now,
        container_status="not_applicable",
        health_score=0,
        health_class="connectivity",
        source="external_proxy",
        capabilities=EgressCapabilities(),
        control_api_reachable=None,
    )
    identity = ServiceIdentity(
        assessment.service_name, "", "", "", assessment.capabilities
    )
    context = RecoveryContext(
        identity,
        assessment,
        policy.snapshot(identity.name, assessment, None, now),
        [],
        [],
        [],
        {},
        {},
    )
    assert policy.decide(context, now).action == "incident"
    assert policy.decide(context, now).incident_type == "endpoint_unhealthy"
    # Contradictory backend details cannot grant capabilities.
    changed = assessment.model_copy(
        update={
            "container_status": "running",
            "control_api_reachable": True,
            "restart_ready": True,
            "available": True,
        }
    )
    assert policy.decide(replace(context, assessment=changed), now).action == "incident"
    changed.health_score = 100
    healthy = replace(
        context,
        assessment=changed,
        snapshot=policy.snapshot(identity.name, changed, context.snapshot, now),
    )
    assert policy.decide(healthy, now).action == "resolve"


# @lat: [[lat.md/egress-tests#External Egress Tests#Watchdog Identity And Incidents]]
def test_external_watchdog_persists_incidents_and_recovers_without_docker(
    tmp_path, fake_gluetun_runtime, monkeypatch
):
    from proxy2vpn.core.egress import EgressObservation

    item = endpoint()
    write_config(tmp_path, [item])
    compose = tmp_path / "compose.yml"
    watchdog = AgentWatchdog(
        compose, runtime=fake_gluetun_runtime, settings=AgentSettings()
    )
    observe = AsyncMock(
        return_value=EgressObservation(0, "connectivity", connectivity=False)
    )
    monkeypatch.setattr(ExternalProxyAdapter, "observe", observe)

    async def exercise():
        state = await watchdog.run_once()
        assert state.status.service_count == 1 and state.status.unhealthy_count == 1
        assert state.services[0].service_name == item.name
        assert state.services[0].source == "external_proxy"
        incident = watchdog.store.load_incidents()[0]
        assert (
            incident.type == "endpoint_unhealthy"
            and incident.recommended_action == "investigate"
        )
        state = await watchdog.run_cycle(state)
        assert len(watchdog.store.load_incidents()) == 1
        assert watchdog.store.load_incidents()[0].id == incident.id
        investigated = await watchdog.investigate_incident(incident.id)
        assert investigated.investigation is not None
        assert "unsupported" in investigated.investigation.action_plan[-1]
        overview = await watchdog.build_remediation_overview(state)
        assert overview["services"][0]["blocks"][0]["type"] == "unsupported_operation"
        with pytest.raises(UnsupportedEgressOperation):
            await watchdog.approve_incident(incident.id)
        with pytest.raises(UnsupportedEgressOperation):
            await watchdog._rotate_service_via_fleet(item.name)
        watchdog.dismiss_incident(incident.id)
        await watchdog.run_cycle(state)
        assert watchdog.store.load_incidents()[0].status == "dismissed"
        observe.return_value = EgressObservation(
            100,
            "healthy",
            available=True,
            connectivity=True,
            current_egress_ip="198.51.100.8",
        )
        # Restore an open incident to verify healthy evidence resolves it by stable id.
        incident.status = "open"
        watchdog.store.append_incident(incident)
        state = await watchdog.run_cycle(state)
        assert state.status.unhealthy_count == 0
        assert state.services[0].current_egress_ip == "198.51.100.8"
        assert watchdog.store.load_incidents()[0].status == "resolved"
        assert not state.actions

    asyncio.run(exercise())
    for name in (
        "inspect",
        "collect_evidence",
        "control_status",
        "restart_tunnel",
        "restore",
        "cleanup_orphans",
    ):
        getattr(fake_gluetun_runtime, name).assert_not_awaited()
    assert not compose.exists()
    assert (
        AgentState.model_validate_json(watchdog.store.state_file.read_text())
        .services[0]
        .service_name
        == item.name
    )


# @lat: [[lat.md/egress-tests#External Egress Tests#Fleet And Status CLI]]
@pytest.mark.parametrize("probe_result", ["healthy", "auth_config"])
def test_external_fleet_status_and_agent_status_json(
    tmp_path, monkeypatch, probe_result
):
    from proxy2vpn.core.egress import EgressObservation

    write_config(tmp_path, [endpoint()])

    async def observe(self, **kwargs):
        if probe_result == "auth_config":
            return ExternalProxyAdapter._authentication_failure()
        return EgressObservation(
            100,
            "healthy",
            available=True,
            connectivity=True,
            current_egress_ip="198.51.100.8",
        )

    monkeypatch.setattr(ExternalProxyAdapter, "observe", observe)
    runner = CliRunner()
    compose = tmp_path / "compose.yml"
    result = runner.invoke(
        app,
        [
            "-f",
            str(compose),
            "fleet",
            "status",
            "--format",
            "json",
            "--show-health",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_services"] == 1
    assert payload["health"]["office-proxy"]["health_class"] == probe_result
    assert payload["health"]["office-proxy"]["authentication"] is (
        None if probe_result == "healthy" else False
    )
    result = runner.invoke(app, ["-f", str(compose), "agent", "run", "--once"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["-f", str(compose), "agent", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["services"][0]["source"] == "external_proxy"


# @lat: [[lat.md/egress-tests#External Egress Tests#Mixed Inventory And Identity Collisions]]
def test_mixed_inventory_preserves_compose_and_blocks_collisions(
    tmp_path, monkeypatch, fake_gluetun_runtime
):
    from proxy2vpn.core.egress import EgressObservation

    compose = tmp_path / "compose.yml"
    original = "services:\n  vpn:\n    image: qmcgaw/gluetun\n    ports: []\n"
    compose.write_text(original)
    write_config(tmp_path, [endpoint()])
    fake_gluetun_runtime.inspect.return_value = RuntimeInspection(
        "running",
        [
            DiagnosticResult(
                check="connectivity", passed=True, message="working", recommendation=""
            )
        ],
    )
    monkeypatch.setattr(
        ExternalProxyAdapter,
        "observe",
        AsyncMock(
            return_value=EgressObservation(0, "connectivity", connectivity=False)
        ),
    )
    watchdog = AgentWatchdog(compose, runtime=fake_gluetun_runtime)
    state = asyncio.run(watchdog.run_once())
    assert state.status.service_count == 2 and state.status.unhealthy_count == 1
    assert state.services[0].service_name == "vpn"
    assert state.services[1].service_name == "office-proxy"
    assert fake_gluetun_runtime.inspect.await_count == 1
    fake_gluetun_runtime.cleanup_orphans.assert_awaited_once()
    fake_gluetun_runtime.restart_tunnel.assert_not_awaited()
    fake_gluetun_runtime.restore.assert_not_awaited()
    assert compose.read_text() == original
    write_config(tmp_path, [endpoint().model_copy(update={"id": "vpn"})])
    with pytest.raises(ValueError, match="collide"):
        watchdog._inventory()
    result = CliRunner().invoke(
        app, ["-f", str(compose), "fleet", "status", "--no-show-allocation"]
    )
    assert result.exit_code == 1 and "collide" in result.output


# @lat: [[lat.md/egress-tests#External Egress Tests#Historical Unsupported Actions]]
@pytest.mark.parametrize(
    "history", ["snapshot_removed", "name_reused", "legacy_incident"]
)
def test_removed_external_endpoint_cannot_fall_back_to_docker(
    tmp_path, monkeypatch, fake_gluetun_runtime, history
):
    from proxy2vpn.core.egress import EgressObservation

    path = write_config(tmp_path, [endpoint()])
    monkeypatch.setattr(
        ExternalProxyAdapter,
        "observe",
        AsyncMock(
            return_value=EgressObservation(0, "connectivity", connectivity=False)
        ),
    )
    watchdog = AgentWatchdog(tmp_path / "compose.yml", runtime=fake_gluetun_runtime)
    asyncio.run(watchdog.run_once())
    incident = watchdog.store.load_incidents()[0]
    incident.recommended_action = "rotate"
    watchdog.store.append_incident(incident)
    path.unlink()
    compose = tmp_path / "compose.yml"
    if history == "name_reused":
        compose.write_text(
            "services:\n  office-proxy:\n    image: qmcgaw/gluetun\n    ports: []\n"
        )
        fake_gluetun_runtime.inspect.return_value = RuntimeInspection(
            "running",
            [
                DiagnosticResult(
                    check="connectivity",
                    passed=True,
                    message="healthy",
                    recommendation="",
                )
            ],
        )
    else:
        compose.write_text("services: {}\n")
    if history == "legacy_incident":
        payload = incident.model_dump(mode="json")
        del payload["source"]
        watchdog.store.incidents_file.write_text(json.dumps(payload) + "\n")

    async def exercise():
        state = await watchdog.run_once()
        assert all(item.source != "external_proxy" for item in state.services)
        stored = watchdog.store.load_incidents()[0]
        assert stored.source == "external_proxy" and stored.status == "open"
        assert stored.id == incident.id
        with pytest.raises(UnsupportedEgressOperation):
            await watchdog.approve_incident(incident.id)
        if history != "name_reused":
            with pytest.raises(UnsupportedEgressOperation):
                await watchdog._rotate_service_via_fleet(incident.service_name)
        investigated = await watchdog.investigate_incident(incident.id)
        assert "no longer configured" in " ".join(investigated.investigation.findings)

    asyncio.run(exercise())
    fake_gluetun_runtime.collect_evidence.assert_not_awaited()
    fake_gluetun_runtime.control_status.assert_not_awaited()
    fake_gluetun_runtime.restore.assert_not_awaited()


# @lat: [[lat.md/egress-tests#External Egress Tests#Batch Cancellation]]
def test_cancelled_batch_closes_all_outstanding_probes(monkeypatch):
    started, closed = set(), set()

    async def exercise():
        both_started = asyncio.Event()

        async def observe(self, **kwargs):
            started.add(self.identity.name)
            if len(started) == 2:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.add(self.identity.name)

        monkeypatch.setattr(ExternalProxyAdapter, "observe", observe)
        task = asyncio.create_task(
            HealthAssessmentService().assess_services(
                [endpoint(), endpoint().model_copy(update={"id": "second"})]
            )
        )
        await both_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == started == {"office-proxy", "second"}

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Real Watchdog Authentication Incident]]
def test_real_proxy_rejection_reaches_watchdog_without_backend_operations(
    tls_material, tmp_path, fake_gluetun_runtime
):
    async def exercise():
        async with controlled_proxy(tls_material, status=407) as (item, _, _, _):
            write_config(tmp_path, [item])
            watchdog = AgentWatchdog(
                tmp_path / "compose.yml", runtime=fake_gluetun_runtime
            )
            state = await watchdog.run_once()
            assert state.services[0].authentication is False
            assert state.services[0].health_class == "auth_config"
            assert state.services[0].current_egress_ip is None
            incident = watchdog.store.load_incidents()[0]
            assert "authentication was rejected" in incident.summary
            assert incident.recommended_action == "investigate"
            assert state.actions == []

    asyncio.run(exercise())
    fake_gluetun_runtime.inspect.assert_not_awaited()
    fake_gluetun_runtime.restart_tunnel.assert_not_awaited()
    fake_gluetun_runtime.restore.assert_not_awaited()
    fake_gluetun_runtime.cleanup_orphans.assert_not_awaited()


# @lat: [[lat.md/egress-tests#External Egress Tests#Upstream HTTP Failures]]
@pytest.mark.parametrize("status", [401, 407, 500, 302])
def test_target_http_failures_do_not_blame_proxy_credentials(
    tls_material, monkeypatch, status
):
    monkeypatch.setenv("PROXY_USERNAME", "operator")
    monkeypatch.setenv("PROXY_PASSWORD", "secret-value")

    async def exercise():
        async with controlled_proxy(
            tls_material, credentials="operator:secret-value", upstream_status=status
        ) as (item, tls, state, _):
            item = item.model_copy(
                update={
                    "credentials": endpoint(
                        credentials={
                            "username_env": "PROXY_USERNAME",
                            "password_env": "PROXY_PASSWORD",
                        }
                    ).credentials
                }
            )
            result = await ExternalProxyAdapter(item, tls_context=tls).observe()
            assert result.health_class == "connectivity"
            assert result.authentication is True
            assert result.connectivity is False and result.current_egress_ip is None
            assert len(state["requests"]) == 1
            assert not any(r.check == "auth_failure" for r in result.results)

    asyncio.run(exercise())


# @lat: [[lat.md/egress-tests#External Egress Tests#Source Change Resets Recovery History]]
@pytest.mark.parametrize("source", ["gluetun", "external_proxy"])
def test_source_change_starts_a_fresh_recovery_episode(source):
    from datetime import timedelta
    from proxy2vpn.core.egress import GLUETUN_CAPABILITIES
    from proxy2vpn.core.services.health_assessment import HealthAssessment

    now = datetime.now(timezone.utc)
    policy = RecoveryPolicy(RecoverySettings(60, 15, 300, 600, 1800))
    assessment = HealthAssessment(
        service_name="reused",
        source=source,
        assessed_at=now,
        container_status="running" if source == "gluetun" else "not_applicable",
        control_api_reachable=source == "gluetun",
        capabilities=GLUETUN_CAPABILITIES
        if source == "gluetun"
        else EgressCapabilities(),
        health_score=0,
        health_class="connectivity",
    )
    previous = policy.snapshot("reused", assessment, None, now)
    previous.source = "external_proxy" if source == "gluetun" else "gluetun"
    previous.consecutive_failures = 8
    previous.degraded_since = now - timedelta(hours=2)
    previous.last_action = "restore"
    previous.last_action_result = "failed"
    snapshot = policy.snapshot("reused", assessment, previous, now)
    assert snapshot.consecutive_failures == 1
    assert snapshot.degraded_since == now
    assert snapshot.last_action is None and snapshot.last_action_result is None
    identity = ServiceIdentity("reused", "", "", "", assessment.capabilities)
    context = RecoveryContext(identity, assessment, snapshot, [], [], [], {}, {})
    assert policy.decide(context, now).action == (
        "restart_tunnel" if source == "gluetun" else "incident"
    )
    retained = policy.snapshot("reused", assessment, snapshot, now)
    assert retained.consecutive_failures == 2


# @lat: [[lat.md/egress-tests#External Egress Tests#New Gluetun First Cycle]]
def test_reused_external_name_gets_normal_gluetun_first_cycle(
    tmp_path, monkeypatch, fake_gluetun_runtime
):
    from datetime import timedelta
    from proxy2vpn.core.egress import EgressObservation

    path = write_config(tmp_path, [endpoint()])
    monkeypatch.setattr(
        ExternalProxyAdapter,
        "observe",
        AsyncMock(
            return_value=EgressObservation(0, "connectivity", connectivity=False)
        ),
    )
    compose = tmp_path / "compose.yml"
    watchdog = AgentWatchdog(
        compose,
        runtime=fake_gluetun_runtime,
        settings=AgentSettings(recheck_delay_seconds=0),
    )
    state = asyncio.run(watchdog.run_once())
    state.services[0].consecutive_failures = 8
    state.services[0].degraded_since -= timedelta(hours=2)
    state.services[0].last_action = "restore"
    state.services[0].last_action_result = "failed"
    watchdog.store.write_state(state)
    incident = watchdog.store.load_incidents()[0]
    path.unlink()
    compose.write_text(
        "services:\n  office-proxy:\n    image: qmcgaw/gluetun\n    ports: []\n"
    )
    fake_gluetun_runtime.inspect.side_effect = [
        RuntimeInspection(
            "running",
            [
                DiagnosticResult(
                    check="connectivity",
                    passed=False,
                    message="unhealthy",
                    recommendation="",
                )
            ],
            control_api_reachable=True,
        ),
        RuntimeInspection(
            "running",
            [
                DiagnosticResult(
                    check="connectivity",
                    passed=True,
                    message="healthy",
                    recommendation="",
                )
            ],
            control_api_reachable=True,
        ),
    ]
    state = asyncio.run(watchdog.run_once())
    fake_gluetun_runtime.restart_tunnel.assert_awaited_once()
    fake_gluetun_runtime.restore.assert_not_awaited()
    assert [action.action for action in state.actions] == ["restart_tunnel"]
    assert state.actions[0].trigger == "first_unhealthy_cycle"
    assert (
        state.services[0].source == "gluetun" and state.services[0].health_score == 100
    )
    stored = watchdog.store.load_incidents()[0]
    assert (
        stored.id == incident.id
        and stored.source == "external_proxy"
        and stored.status == "open"
    )


# @lat: [[lat.md/egress-tests#External Egress Tests#Gluetun Rotation After Source Reuse]]
@pytest.mark.parametrize("operation", ["automatic", "approved"])
@pytest.mark.parametrize("historical_status", ["open", "resolved", "dismissed"])
def test_current_gluetun_rotation_ignores_external_incident_history(
    tmp_path, monkeypatch, fake_gluetun_runtime, operation, historical_status
):
    from datetime import timedelta
    from types import SimpleNamespace
    from proxy2vpn.agent.models import ActionRecord, AgentIncident
    from proxy2vpn.core.egress import EgressObservation

    path = write_config(tmp_path, [endpoint()])
    monkeypatch.setattr(
        ExternalProxyAdapter,
        "observe",
        AsyncMock(
            return_value=EgressObservation(0, "connectivity", connectivity=False)
        ),
    )
    compose = tmp_path / "compose.yml"
    watchdog = AgentWatchdog(compose, runtime=fake_gluetun_runtime)
    asyncio.run(watchdog.run_once())
    historical = watchdog.store.load_incidents()[0]
    historical.status = historical_status
    historical.recommended_action = "rotate"
    watchdog.store.append_incident(historical)
    path.unlink()
    compose.write_text(
        "services:\n  office-proxy:\n    image: qmcgaw/gluetun\n    ports: []\n"
    )
    fake_gluetun_runtime.inspect.return_value = RuntimeInspection(
        "running",
        [
            DiagnosticResult(
                check="connectivity", passed=True, message="healthy", recommendation=""
            )
        ],
        control_api_reachable=True,
    )
    state = asyncio.run(watchdog.run_once())
    now = datetime.now(timezone.utc)
    state.services[0].consecutive_failures = 2
    state.services[0].degraded_since = now - timedelta(hours=2)
    state.actions.append(
        ActionRecord(
            ts=now,
            service_name=historical.service_name,
            action="restore",
            trigger="automatic_remediation",
            result="failed",
        )
    )
    watchdog.store.write_state(state)
    fake_gluetun_runtime.inspect.return_value = RuntimeInspection(
        "running",
        [
            DiagnosticResult(
                check="connectivity",
                passed=False,
                message="unhealthy",
                recommendation="",
            )
        ],
        control_api_reachable=True,
    )
    fleet = SimpleNamespace(
        rotate_service=AsyncMock(
            return_value=SimpleNamespace(success=True, errors=[], rotation_changes=[])
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("proxy2vpn.agent.runtime.FleetStateManager", lambda path: fleet)

    async def exercise():
        with pytest.raises(UnsupportedEgressOperation):
            await watchdog.approve_incident(historical.id)
        if operation == "automatic":
            updated = await watchdog.run_once()
            assert updated.actions[-1].action == "rotate"
            assert updated.actions[-1].trigger == "automatic_remediation"
            assert updated.actions[-1].result == "success"
        else:
            current = AgentIncident(
                id="current-gluetun-rotation",
                service_name=historical.service_name,
                source="gluetun",
                type="rotation_exhausted",
                severity="medium",
                created_at=now,
                updated_at=now,
                summary="Current VPN needs rotation",
                recommended_action="rotate",
                approval_required=True,
            )
            watchdog.store.append_incident(current)
            approved = await watchdog.approve_incident(current.id)
            assert approved.source == "gluetun" and approved.status == "resolved"
        retained = next(
            item for item in watchdog.store.load_incidents() if item.id == historical.id
        )
        assert (
            retained.source == "external_proxy" and retained.status == historical_status
        )
        with pytest.raises(UnsupportedEgressOperation):
            await watchdog.approve_incident(historical.id)

    asyncio.run(exercise())
    fleet.rotate_service.assert_awaited_once()
    assert fleet.rotate_service.await_args.args[0] == historical.service_name
    fleet.close.assert_awaited_once()
    fake_gluetun_runtime.restart_tunnel.assert_not_awaited()
    fake_gluetun_runtime.restore.assert_not_awaited()
