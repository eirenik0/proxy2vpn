import json

from typer.testing import CliRunner

from proxy2vpn.agent.config import AgentSettings
from proxy2vpn.agent.models import (
    AgentIncident,
    AgentState,
    AgentStatus,
    ServiceSnapshot,
)
from proxy2vpn.agent.runtime import utc_now
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.cli.main import app
from proxy2vpn.core import config
from proxy2vpn.core.services.diagnostics import DiagnosticResult
import proxy2vpn.agent.runtime as agent_runtime


def _write_agent_compose(tmp_path):
    compose_file = tmp_path / "compose.yml"
    (tmp_path / "env.test").write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    (tmp_path / config.CONTROL_AUTH_CONFIG_FILE).write_text(
        '[[roles]]\nname = "proxy2vpn"\nauth = "none"\n'
    )
    compose_file.write_text(
        """
x-vpn-base-test: &vpn-base-test
  image: qmcgaw/gluetun
  cap_add:
    - NET_ADMIN
  devices:
    - /dev/net/tun:/dev/net/tun
  env_file:
    - env.test

services:
  protonvpn-united-states-new-york:
    <<: *vpn-base-test
    ports:
      - "0.0.0.0:9999:8888/tcp"
      - "127.0.0.1:30000:8000/tcp"
    environment:
      - VPN_SERVICE_PROVIDER=protonvpn
      - SERVER_CITIES=New York
      - SERVER_COUNTRIES=United States
    labels:
      vpn.type: vpn
      vpn.port: "9999"
      vpn.control_port: "30000"
      vpn.profile: test
      vpn.provider: protonvpn
      vpn.location: New York
""".strip()
    )
    return compose_file


class DummyContainer:
    def __init__(self, status: str = "running"):
        self.status = status

    def reload(self):
        return None


def healthy_results():
    return [
        DiagnosticResult(
            check="connectivity",
            passed=True,
            message="VPN working",
            recommendation="",
        )
    ]


def test_agent_run_once_cli_creates_state(tmp_path, monkeypatch):
    compose_file = _write_agent_compose(tmp_path)

    class DummyControlClient:
        def __init__(self, base_url):
            self.base_url = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def status(self):
            return {"status": "running"}

    async def _sleep(*args, **kwargs):
        return None

    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(agent_runtime.asyncio, "sleep", _sleep)
    monkeypatch.setattr(agent_runtime.asyncio, "to_thread", _to_thread)
    monkeypatch.setattr(agent_runtime, "GluetunControlClient", DummyControlClient)
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "get_container_by_service_name",
        lambda name: DummyContainer("running"),
    )
    monkeypatch.setattr(
        agent_runtime.docker_ops,
        "analyze_container_logs",
        lambda *args, **kwargs: healthy_results(),
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["--compose-file", str(compose_file), "agent", "run", "--once"]
    )

    assert result.exit_code == 0
    assert "Agent cycle complete" in result.output
    assert AgentStateStore(compose_file).state_file.exists()


def test_agent_status_and_incidents_json_are_machine_readable(tmp_path):
    compose_file = _write_agent_compose(tmp_path)
    store = AgentStateStore(compose_file)
    state = AgentState(
        status=AgentStatus(
            compose_path=str(compose_file),
            daemon_mode="once",
            started_at=utc_now(),
            last_loop_at=utc_now(),
            interval_seconds=AgentSettings().interval_seconds,
            service_count=1,
            unhealthy_count=1,
            last_error=None,
            llm_mode="disabled",
        ),
        services=[
            ServiceSnapshot(
                service_name="protonvpn-united-states-new-york",
                container_status="running",
                health_score=0,
                consecutive_failures=2,
                last_check_at=utc_now(),
                last_action="restore",
                last_action_result="failed",
            )
        ],
    )
    store.write_state(state)
    store.append_incident(
        AgentIncident(
            id="incident123",
            service_name="protonvpn-united-states-new-york",
            type="rotation_required",
            severity="medium",
            status="open",
            created_at=utc_now(),
            updated_at=utc_now(),
            failure_count=2,
            summary="Needs rotation",
            recommended_action="rotate",
            approval_required=True,
        )
    )

    runner = CliRunner()
    status_result = runner.invoke(
        app, ["--compose-file", str(compose_file), "agent", "status", "--json"]
    )
    incidents_result = runner.invoke(
        app, ["--compose-file", str(compose_file), "agent", "incidents", "--json"]
    )

    assert status_result.exit_code == 0
    assert incidents_result.exit_code == 0

    status_payload = json.loads(status_result.output)
    incidents_payload = json.loads(incidents_result.output)

    assert set(status_payload.keys()) == {"status", "services", "actions"}
    assert set(status_payload["status"].keys()) == {
        "compose_path",
        "daemon_mode",
        "started_at",
        "last_loop_at",
        "interval_seconds",
        "service_count",
        "unhealthy_count",
        "last_error",
        "llm_mode",
    }
    assert set(incidents_payload.keys()) == {"incidents"}
    assert incidents_payload["incidents"][0]["recommended_action"] == "rotate"
