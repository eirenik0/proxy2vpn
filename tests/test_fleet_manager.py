import pytest
from pathlib import Path

from proxy2vpn.fleet_manager import FleetConfig, FleetManager


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
