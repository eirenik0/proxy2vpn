import asyncio
from datetime import datetime

import pytest

from proxy2vpn.adapters import fleet_state_manager as fleet_state_manager_mod
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.core.models import Profile, VPNService


def test_fleet_state_manager_reinitializes_for_new_compose_path(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_a = tmp_path / "a.yml"
    compose_b = tmp_path / "b.yml"
    ComposeManager.create_initial_compose(compose_a, force=True)
    ComposeManager.create_initial_compose(compose_b, force=True)

    first = fleet_state_manager_mod.FleetStateManager(str(compose_a))
    second = fleet_state_manager_mod.FleetStateManager(str(compose_b))

    assert first is not second
    assert first.compose_path == compose_a.resolve()
    assert second.compose_path == compose_b.resolve()


def test_execute_single_rotation_updates_service_location(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    env_path = tmp_path / "test.env"
    env_path.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    manager.add_profile(Profile(name="test", env_file=str(env_path)))

    service = VPNService.create(
        name="protonvpn-canada-toronto",
        port=20000,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "Toronto",
            "SERVER_COUNTRIES": "Canada",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "30000",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "Toronto",
        },
    )
    manager.add_service(service)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )

    async def fake_ip(service_name):
        return None

    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)

    final_name, final_location = asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
            )
        )
    )

    with pytest.raises(KeyError):
        fleet_manager.compose_manager.get_service("protonvpn-canada-toronto")
    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-montreal"
    )
    assert final_name == "protonvpn-canada-montreal"
    assert final_location == "Montreal"
    assert updated_service.name == "protonvpn-canada-montreal"
    assert updated_service.location == "Montreal"
    assert updated_service.environment["SERVER_CITIES"] == "Montreal"
    assert updated_service.labels["vpn.location"] == "Montreal"


def test_execute_single_rotation_retries_until_egress_ip_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    env_path = tmp_path / "test.env"
    env_path.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    manager.add_profile(Profile(name="test", env_file=str(env_path)))

    service = VPNService.create(
        name="protonvpn-canada-toronto",
        port=20000,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "Toronto",
            "SERVER_COUNTRIES": "Canada",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "30000",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "Toronto",
        },
    )
    manager.add_service(service)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    applied_locations: list[str] = []

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter(["198.51.100.10", "198.51.100.10", "203.0.113.20"])

    async def fake_ip(service_name):
        return next(ip_sequence)

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "recreate_vpn_container",
        lambda service, profile: applied_locations.append(service.location),
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)

    final_name, final_location = asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
                candidate_locations=["Montreal", "Vancouver"],
            )
        )
    )

    with pytest.raises(KeyError):
        fleet_manager.compose_manager.get_service("protonvpn-canada-toronto")
    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-vancouver"
    )
    assert final_name == "protonvpn-canada-vancouver"
    assert final_location == "Vancouver"
    assert updated_service.name == "protonvpn-canada-vancouver"
    assert updated_service.location == "Vancouver"
    assert applied_locations == ["Montreal", "Vancouver"]


def test_execute_single_rotation_rolls_back_when_all_candidates_keep_same_ip(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    env_path = tmp_path / "test.env"
    env_path.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    manager.add_profile(Profile(name="test", env_file=str(env_path)))

    service = VPNService.create(
        name="protonvpn-canada-toronto",
        port=20000,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "Toronto",
            "SERVER_COUNTRIES": "Canada",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "30000",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "Toronto",
        },
    )
    manager.add_service(service)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    applied_locations: list[str] = []

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter(["198.51.100.10", "198.51.100.10", "198.51.100.10"])

    async def fake_ip(service_name):
        return next(ip_sequence)

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "recreate_vpn_container",
        lambda service, profile: applied_locations.append(service.location),
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)

    with pytest.raises(Exception, match="new egress IP"):
        asyncio.run(
            fleet_manager._execute_single_rotation(
                fleet_state_manager_mod.ServiceRotationPlan(
                    service_name="protonvpn-canada-toronto",
                    old_location="Toronto",
                    new_location="Montreal",
                    reason="health_check_failed",
                    candidate_locations=["Montreal", "Vancouver"],
                )
            )
        )

    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-toronto"
    )
    assert updated_service.name == "protonvpn-canada-toronto"
    assert updated_service.location == "Toronto"
    assert applied_locations == ["Montreal", "Vancouver", "Toronto"]


def test_execute_single_rotation_preserves_port_suffix_when_renaming(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    env_path = tmp_path / "test.env"
    env_path.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    manager.add_profile(Profile(name="test", env_file=str(env_path)))

    service = VPNService.create(
        name="protonvpn-canada-toronto-20000",
        port=20000,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "Toronto",
            "SERVER_COUNTRIES": "Canada",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "30000",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "Toronto",
        },
    )
    manager.add_service(service)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    async def fake_ip(service_name):
        return None

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)

    final_name, final_location = asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto-20000",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
            )
        )
    )

    with pytest.raises(KeyError):
        fleet_manager.compose_manager.get_service("protonvpn-canada-toronto-20000")
    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-montreal-20000"
    )
    assert final_name == "protonvpn-canada-montreal-20000"
    assert final_location == "Montreal"
    assert updated_service.name == "protonvpn-canada-montreal-20000"
