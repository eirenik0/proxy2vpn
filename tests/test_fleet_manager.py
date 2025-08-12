import asyncio
import pytest
from pathlib import Path

from proxy2vpn.fleet_manager import (
    DeploymentPlan,
    FleetConfig,
    FleetManager,
    ServicePlan,
)


@pytest.fixture
def fleet_manager():
    return FleetManager(compose_file_path=Path("tests/test_compose.yml"))


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


def test_deploy_fleet_rolls_back_on_error(monkeypatch, fleet_manager, capsys):
    plan = DeploymentPlan(provider="prov")
    plan.services = [
        ServicePlan(
            name="svc1",
            profile="test",
            location="L1",
            country="C",
            port=10000,
            provider="prov",
        ),
        ServicePlan(
            name="svc2",
            profile="test",
            location="L2",
            country="C",
            port=10001,
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
