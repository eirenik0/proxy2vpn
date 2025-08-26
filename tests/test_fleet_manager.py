import asyncio
import pytest
from pathlib import Path

from proxy2vpn.adapters.fleet_manager import (
    DeploymentPlan,
    FleetConfig,
    FleetManager,
    ServicePlan,
)
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.core.models import Profile, VPNService


@pytest.fixture
def fleet_manager():
    return FleetManager(compose_file_path=Path("tests/test_fleet_compose.yml"))


def test_plan_deployment_basic_allocation(fleet_manager, monkeypatch):
    def fake_list_cities(provider, country):
        return {
            "A": ["City1"],
            "B": ["City2"],
        }[country]

    monkeypatch.setattr(fleet_manager.server_manager, "list_cities", fake_list_cities)

    config = FleetConfig(
        provider="prov",
        countries=["A", "B"],
        profiles={"acc1": 1, "acc2": 1},
        port_start=30000,
    )

    plan = fleet_manager.plan_deployment(config)

    assert [s.name for s in plan.services] == [
        "prov-a-city1",
        "prov-b-city2",
    ]
    assert [s.profile for s in plan.services] == ["acc1", "acc2"]
    assert [s.port for s in plan.services] == [30000, 30001]
    assert [s.country for s in plan.services] == ["A", "B"]


def test_plan_deployment_sanitizes_and_limits(fleet_manager, monkeypatch):
    def fake_list_cities(provider, country):
        return {
            "United States": [
                "New York",
                "Los Angeles",
                "Chicago",
            ]
        }[country]

    monkeypatch.setattr(fleet_manager.server_manager, "list_cities", fake_list_cities)

    config = FleetConfig(
        provider="prov",
        countries=["United States"],
        profiles={"acc1": 1},
        port_start=21000,
    )

    plan = fleet_manager.plan_deployment(config)

    assert len(plan.services) == 1
    service = plan.services[0]
    assert service.name == "prov-united-states-new-york"
    assert service.profile == "acc1"
    assert service.port == 21000


def test_plan_deployment_unique_ips(fleet_manager):
    fleet_manager.server_manager.data = {
        "version": 1,
        "protonvpn": {
            "servers": [
                {
                    "country": "A",
                    "city": "City1",
                    "hostname": "host1",
                    "ips": ["1.1.1.1"],
                },
                {
                    "country": "A",
                    "city": "City1",
                    "hostname": "host2",
                    "ips": ["1.1.1.1"],
                },
                {
                    "country": "A",
                    "city": "City2",
                    "hostname": "host3",
                    "ips": ["2.2.2.2"],
                },
                {
                    "country": "B",
                    "city": "City3",
                    "hostname": "host4",
                    "ips": ["3.3.3.3"],
                },
            ]
        },
    }
    config = FleetConfig(
        provider="protonvpn",
        countries=["A", "B"],
        profiles={"acc1": 3},
        port_start=40000,
        unique_ips=True,
    )
    plan = fleet_manager.plan_deployment(config)
    assert len(plan.services) == 3
    hostnames = [s.hostname for s in plan.services]
    assert len(set(hostnames)) == 3
    ips = [s.ip for s in plan.services]
    assert len(set(ips)) == 3


def test_create_service_from_plan_includes_country_env(fleet_manager):
    sp = ServicePlan(
        name="prov-a-city1",
        profile="acc",
        location="City1",
        country="A",
        port=20000,
        control_port=30000,
        provider="prov",
    )
    svc = fleet_manager._create_service_from_plan(sp)
    assert svc.environment["SERVER_CITIES"] == "City1"
    assert svc.environment["SERVER_COUNTRIES"] == "A"


def test_plan_deployment_missing_profile(fleet_manager, monkeypatch):
    def fake_list_cities(provider, country):
        return {"A": ["City1"]}[country]

    monkeypatch.setattr(fleet_manager.server_manager, "list_cities", fake_list_cities)

    config = FleetConfig(
        provider="prov",
        countries=["A"],
        profiles={"missing": 1},
        port_start=30000,
    )

    with pytest.raises(ValueError):
        fleet_manager.plan_deployment(config)


def test_deploy_fleet_rolls_back_on_error(monkeypatch, fleet_manager, capsys):
    plan = DeploymentPlan(provider="prov")
    plan.services = [
        ServicePlan(
            name="svc1",
            profile="test",
            location="L1",
            country="C",
            port=10000,
            control_port=30000,
            provider="prov",
        ),
        ServicePlan(
            name="svc2",
            profile="test",
            location="L2",
            country="C",
            port=10001,
            control_port=30001,
            provider="prov",
        ),
    ]

    added = []

    def fake_add_service(service):
        if service.name == "svc2":
            raise RuntimeError("boom")
        added.append(service.name)

    removed = []

    def fake_remove_service(name):
        removed.append(name)

    stop_calls = []

    def fake_stop(name):
        stop_calls.append(name)

    remove_calls = []

    def fake_remove(name):
        remove_calls.append(name)

    monkeypatch.setattr(fleet_manager.compose_manager, "add_service", fake_add_service)
    monkeypatch.setattr(
        fleet_manager.compose_manager, "remove_service", fake_remove_service
    )
    monkeypatch.setattr("proxy2vpn.fleet_manager.stop_container", fake_stop)
    monkeypatch.setattr("proxy2vpn.fleet_manager.remove_container", fake_remove)
    monkeypatch.setattr("proxy2vpn.fleet_manager.ensure_network", lambda force: None)

    result = asyncio.run(
        fleet_manager.deploy_fleet(plan, validate_servers=False, parallel=False)
    )

    assert result.deployed == 0
    assert result.failed == 2
    assert removed == ["svc1"]
    assert stop_calls == ["svc1"]
    assert remove_calls == ["svc1"]

    out = capsys.readouterr().out
    assert "Rolled back service: svc1" in out
    assert "Stopped and removed container: svc1" in out


def test_deploy_fleet_skips_invalid_locations(monkeypatch, fleet_manager, capsys):
    plan = DeploymentPlan(provider="prov")
    plan.services = [
        ServicePlan(
            name="svc1",
            profile="test",
            location="good",
            country="C",
            port=10000,
            control_port=30000,
            provider="prov",
        ),
        ServicePlan(
            name="svc2",
            profile="test",
            location="bad",
            country="C",
            port=10001,
            control_port=30001,
            provider="prov",
        ),
    ]

    def fake_validate_location(provider, location):
        return location != "bad"

    monkeypatch.setattr(
        fleet_manager.server_manager, "validate_location", fake_validate_location
    )

    added = []
    start_calls: list[str] = []

    def fake_add_service(service):
        added.append(service.name)

    async def fake_start(service_names, force):
        start_calls.extend(service_names)

    monkeypatch.setattr(fleet_manager.compose_manager, "add_service", fake_add_service)
    monkeypatch.setattr(fleet_manager, "_start_services_sequential", fake_start)
    monkeypatch.setattr("proxy2vpn.fleet_manager.ensure_network", lambda force: None)

    result = asyncio.run(
        fleet_manager.deploy_fleet(plan, validate_servers=True, parallel=False)
    )

    assert added == ["svc1"]
    assert start_calls == ["svc1"]
    assert result.deployed == 1
    assert result.failed == 1

    out = capsys.readouterr().out
    assert "Invalid location bad for prov" in out
    assert "Skipping 1 invalid service" in out


def test_get_fleet_status_reconstructs_allocator(tmp_path):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    env1 = tmp_path / "acc1.env"
    env1.write_text("KEY=value\n")
    env2 = tmp_path / "acc2.env"
    env2.write_text("KEY=value\n")

    manager.add_profile(Profile(name="acc1", env_file=str(env1)))
    manager.add_profile(Profile(name="acc2", env_file=str(env2)))

    svc1 = VPNService.create(
        name="prov-a-city1",
        port=20000,
        control_port=31000,
        provider="prov",
        profile="acc1",
        location="city1",
        environment={
            "VPN_SERVICE_PROVIDER": "prov",
            "SERVER_CITIES": "city1",
            "SERVER_COUNTRIES": "a",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "31000",
            "vpn.provider": "prov",
            "vpn.profile": "acc1",
            "vpn.location": "city1",
        },
    )
    svc2 = VPNService.create(
        name="prov-a-city2",
        port=20001,
        control_port=31001,
        provider="prov",
        profile="acc1",
        location="city2",
        environment={
            "VPN_SERVICE_PROVIDER": "prov",
            "SERVER_CITIES": "city2",
            "SERVER_COUNTRIES": "a",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20001",
            "vpn.control_port": "31001",
            "vpn.provider": "prov",
            "vpn.profile": "acc1",
            "vpn.location": "city2",
        },
    )
    svc3 = VPNService.create(
        name="prov-b-city3",
        port=20002,
        control_port=31002,
        provider="prov",
        profile="acc2",
        location="city3",
        environment={
            "VPN_SERVICE_PROVIDER": "prov",
            "SERVER_CITIES": "city3",
            "SERVER_COUNTRIES": "b",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20002",
            "vpn.control_port": "31002",
            "vpn.provider": "prov",
            "vpn.profile": "acc2",
            "vpn.location": "city3",
        },
    )

    manager.add_service(svc1)
    manager.add_service(svc2)
    manager.add_service(svc3)

    fm = FleetManager(compose_file_path=compose_path)
    status = fm.get_fleet_status()

    assert status["total_services"] == 3
    assert status["profile_counts"] == {"acc1": 2, "acc2": 1}
    assert status["country_counts"] == {"a": 2, "b": 1}


def test_start_services_parallel_uses_helper(monkeypatch, fleet_manager):
    from proxy2vpn import docker_ops

    calls: list[tuple[str, str, bool]] = []

    def fake_start(service, profile, force):
        calls.append((service.name, profile.name, force))

    monkeypatch.setattr(docker_ops, "start_vpn_service", fake_start)

    asyncio.run(
        fleet_manager._start_services_parallel(["testvpn1", "testvpn2"], force=False)
    )

    assert sorted(c[0] for c in calls) == ["testvpn1", "testvpn2"]
    assert all(not c[2] for c in calls)


def test_start_services_sequential_uses_helper(monkeypatch, fleet_manager):
    from proxy2vpn import docker_ops

    calls: list[tuple[str, str, bool]] = []

    def fake_start(service, profile, force):
        calls.append((service.name, profile.name, force))

    monkeypatch.setattr(docker_ops, "start_vpn_service", fake_start)

    asyncio.run(
        fleet_manager._start_services_sequential(["testvpn1", "testvpn2"], force=True)
    )

    assert calls == [("testvpn1", "test", True), ("testvpn2", "test", True)]


def test_get_service_status_counts(monkeypatch):
    from proxy2vpn.adapters.docker_ops import get_service_status_counts

    class FakeContainer:
        def __init__(self, name, status):
            self.name = name
            self.status = status

    containers = [
        FakeContainer("svc1", "running"),
        FakeContainer("svc2", "exited"),
    ]

    monkeypatch.setattr(
        "proxy2vpn.docker_ops.get_vpn_containers", lambda all=True: containers
    )

    running, stopped = get_service_status_counts(["svc1", "svc2", "svc3"])
    assert running == 1
    assert stopped == 2
