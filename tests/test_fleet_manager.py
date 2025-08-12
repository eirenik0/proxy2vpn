import pytest
from pathlib import Path

from proxy2vpn.fleet_manager import FleetConfig, FleetManager
from proxy2vpn.compose_manager import ComposeManager
from proxy2vpn.models import Profile, VPNService


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

    svc1 = VPNService(
        name="prov-a-city1",
        port=20000,
        provider="prov",
        profile="acc1",
        location="city1",
        environment={"VPN_SERVICE_PROVIDER": "prov", "SERVER_CITIES": "city1"},
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.provider": "prov",
            "vpn.profile": "acc1",
            "vpn.location": "city1",
        },
    )
    svc2 = VPNService(
        name="prov-a-city2",
        port=20001,
        provider="prov",
        profile="acc1",
        location="city2",
        environment={"VPN_SERVICE_PROVIDER": "prov", "SERVER_CITIES": "city2"},
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20001",
            "vpn.provider": "prov",
            "vpn.profile": "acc1",
            "vpn.location": "city2",
        },
    )
    svc3 = VPNService(
        name="prov-b-city3",
        port=20002,
        provider="prov",
        profile="acc2",
        location="city3",
        environment={"VPN_SERVICE_PROVIDER": "prov", "SERVER_CITIES": "city3"},
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20002",
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
