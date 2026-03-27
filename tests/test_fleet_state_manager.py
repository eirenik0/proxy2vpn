import asyncio
from datetime import datetime
import logging

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

    async def fake_check(service_name, timeout=None):
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

    async def fake_ip(service_name, timeout=None):
        return None

    async def fake_vpn_test(service_name, timeout=3):
        return True

    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    change = asyncio.run(
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
    assert change.final_service_name == "protonvpn-canada-montreal"
    assert change.new_location == "Canada / Montreal"
    assert change.old_location == "Canada / Toronto"
    assert updated_service.name == "protonvpn-canada-montreal"
    assert updated_service.location == "Montreal"
    assert updated_service.environment["SERVER_COUNTRIES"] == "Canada"
    assert updated_service.environment["SERVER_CITIES"] == "Montreal"
    assert updated_service.labels["vpn.country"] == "Canada"
    assert updated_service.labels["vpn.location"] == "Montreal"


def test_batch_health_check_uses_single_container_snapshot(monkeypatch, tmp_path):
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

    service_names = []
    for idx, city in enumerate(["Ashburn", "Atlanta", "Boston"], start=1):
        service = VPNService.create(
            name=f"protonvpn-united-states-{city.lower()}",
            port=20000 + idx,
            control_port=30000 + idx,
            provider="protonvpn",
            profile="test",
            location=city,
            environment={
                "VPN_SERVICE_PROVIDER": "protonvpn",
                "SERVER_CITIES": city,
                "SERVER_COUNTRIES": "United States",
            },
            labels={
                "vpn.type": "vpn",
                "vpn.port": str(20000 + idx),
                "vpn.control_port": str(30000 + idx),
                "vpn.provider": "protonvpn",
                "vpn.profile": "test",
                "vpn.location": city,
            },
        )
        manager.add_service(service)
        service_names.append(service.name)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    fleet_manager._sync_services_from_compose()

    class Container:
        def __init__(self, name: str):
            self.name = name
            self.status = "running"

        def reload(self):
            return None

    containers = [Container(name) for name in service_names]
    lookup_calls = []

    monkeypatch.setattr(
        fleet_state_manager_mod,
        "get_vpn_containers",
        lambda all=True: lookup_calls.append(all) or containers,
    )

    async def fake_to_thread(func, *args, **kwargs):
        return "198.51.100.10"

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "to_thread", fake_to_thread)

    async def fake_get_ip(container, timeout=None):
        return "203.0.113.50"

    monkeypatch.setattr(fleet_state_manager_mod, "get_container_ip_async", fake_get_ip)

    from proxy2vpn.core.services.diagnostics import DiagnosticResult
    from proxy2vpn.adapters import docker_ops

    def fake_analyze_container_logs(
        name, lines=100, analyzer=None, timeout=5, direct_ip=None
    ):
        return [
            DiagnosticResult(
                check="logs",
                passed=True,
                message="ok",
                recommendation="",
            )
        ]

    monkeypatch.setattr(
        docker_ops, "analyze_container_logs", fake_analyze_container_logs
    )
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "get_container_by_service_name",
        lambda name: (_ for _ in ()).throw(
            AssertionError("unexpected container lookup")
        ),
    )

    results = asyncio.run(fleet_manager._batch_health_check(service_names))

    assert lookup_calls == [True]
    assert set(results) == set(service_names)
    assert all(result.is_healthy for result in results.values())


def test_execute_single_rotation_preserves_server_hostname(monkeypatch, tmp_path):
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
            "SERVER_HOSTNAMES": "us10562.nordvpn.com",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "20000",
            "vpn.control_port": "30000",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "Toronto",
            "vpn.hostname": "us10562.nordvpn.com",
        },
    )
    manager.add_service(service)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    async def fake_ip(service_name, timeout=None):
        return None

    async def fake_vpn_test(service_name, timeout=3):
        return True

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
            )
        )
    )

    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-montreal"
    )
    assert updated_service.environment["SERVER_HOSTNAMES"] == "us10562.nordvpn.com"
    assert updated_service.labels["vpn.hostname"] == "us10562.nordvpn.com"
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

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter(["198.51.100.10", "198.51.100.10", "203.0.113.20"])

    async def fake_ip(service_name, timeout=None):
        return next(ip_sequence)

    async def fake_vpn_test(service_name, timeout=3):
        return True

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
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    change = asyncio.run(
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
        "protonvpn-canada-montreal"
    )
    assert change.final_service_name == "protonvpn-canada-montreal"
    assert change.new_location == "Canada / Montreal"
    assert change.old_location == "Canada / Toronto"
    assert change.candidate_locations == ["Canada / Montreal", "Canada / Vancouver"]
    assert change.attempted_locations == ["Canada / Montreal"]
    assert updated_service.name == "protonvpn-canada-montreal"
    assert updated_service.location == "Montreal"
    assert applied_locations == ["Montreal"]


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

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter(["198.51.100.10", "198.51.100.10", "198.51.100.10"])

    async def fake_ip(service_name, timeout=None):
        return next(ip_sequence)

    async def fake_vpn_test(service_name, timeout=3):
        return True

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
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

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

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    async def fake_ip(service_name, timeout=None):
        return None

    async def fake_vpn_test(service_name, timeout=3):
        return True

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    change = asyncio.run(
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
    assert change.final_service_name == "protonvpn-canada-montreal-20000"
    assert change.new_location == "Canada / Montreal"
    assert updated_service.name == "protonvpn-canada-montreal-20000"


def test_execute_single_rotation_retries_same_city_before_switching_candidates(
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
    verification_calls: list[str] = []
    applied_locations: list[str] = []

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    vpn_test_results = iter([False, False, False, True])

    async def fake_vpn_test(service_name, timeout=3):
        verification_calls.append(service_name)
        return next(vpn_test_results)

    ip_sequence = iter(["198.51.100.10", "203.0.113.20"])

    async def fake_ip(service_name, timeout=None):
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
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    change = asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto",
                old_location="Toronto",
                new_location="Montreal",
                reason="health_check_failed",
                candidate_locations=["Montreal", "Vancouver"],
            ),
            fleet_state_manager_mod.OperationConfig(
                health_check_timeout=30,
                rotation_verification_attempts=3,
                rotation_verification_delay_seconds=0,
            ),
        )
    )

    assert change.final_service_name == "protonvpn-canada-vancouver"
    assert verification_calls == [
        "protonvpn-canada-montreal",
        "protonvpn-canada-montreal",
        "protonvpn-canada-montreal",
        "protonvpn-canada-vancouver",
    ]
    assert applied_locations == ["Montreal", "Vancouver"]


def test_rank_rotation_candidates_skips_recently_failed_cities(monkeypatch, tmp_path):
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

    toronto = VPNService.create(
        name="protonvpn-canada-toronto",
        port=20000,
        control_port=30000,
        provider="protonvpn",
        profile="test",
        location="Toronto",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_COUNTRIES": "Canada",
            "SERVER_CITIES": "Toronto",
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
    manager.add_service(toronto)

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    fleet_manager.services = {toronto.name: toronto}
    fleet_manager.server_manager.data = {
        "protonvpn": {
            "servers": [
                {"country": "Canada", "city": "Montreal", "ips": ["1.1.1.1"]},
                {"country": "Canada", "city": "Vancouver", "ips": ["2.2.2.2"]},
            ]
        }
    }
    fleet_manager._mark_bad_rotation_city("protonvpn", "Canada", "Montreal")

    candidates = fleet_manager._rank_rotation_candidates(
        service=toronto,
        country="Canada",
        available_cities=["Toronto", "Montreal", "Vancouver"],
        config=fleet_state_manager_mod.OperationConfig(
            criteria=fleet_state_manager_mod.RotationCriteria.PERFORMANCE,
            bad_city_cooldown_seconds=3600,
        ),
    )

    assert candidates == ["Vancouver"]


def test_execute_single_rotation_fails_after_three_server_attempts(
    monkeypatch, tmp_path, caplog
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

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    async def fake_ip(service_name, timeout=None):
        return "198.51.100.10"

    async def fake_vpn_test(service_name, timeout=3):
        return False

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
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    with caplog.at_level(logging.INFO):
        with pytest.raises(Exception, match="stopped after 3 rotation attempts"):
            asyncio.run(
                fleet_manager._execute_single_rotation(
                    fleet_state_manager_mod.ServiceRotationPlan(
                        service_name="protonvpn-canada-toronto",
                        old_location="Toronto",
                        new_location="Montreal",
                        reason="health_check_failed",
                        candidate_locations=[
                            "Montreal",
                            "Vancouver",
                            "Calgary",
                            "Edmonton",
                        ],
                    ),
                    fleet_state_manager_mod.OperationConfig(
                        rotation_attempt_limit=3,
                        rotation_verification_attempts=1,
                        rotation_verification_delay_seconds=0,
                    ),
                )
            )

    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-toronto"
    )
    assert updated_service.location == "Toronto"
    assert applied_locations == ["Montreal", "Vancouver", "Calgary", "Toronto"]
    assert "rotation_attempt_started" in caplog.text
    assert "rotation_attempt_failed" in caplog.text
    assert "rotation_attempt_limit_reached" in caplog.text


def test_create_rotation_plan_orders_same_country_before_fallback_countries(
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
    manager.add_service(
        VPNService.create(
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
    )

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    fleet_manager._sync_services_from_compose()
    monkeypatch.setattr(
        fleet_manager.server_manager,
        "list_countries",
        lambda provider: ["Canada", "Netherlands", "Germany"],
    )
    monkeypatch.setattr(
        fleet_manager.server_manager,
        "list_cities",
        lambda provider, country: {
            "Canada": ["Toronto", "Vancouver", "Montreal"],
            "Netherlands": ["Rotterdam", "Amsterdam"],
            "Germany": ["Berlin"],
        }[country],
    )

    plan = fleet_manager._create_rotation_plan(
        ["protonvpn-canada-toronto"],
        fleet_state_manager_mod.OperationConfig(
            criteria=fleet_state_manager_mod.RotationCriteria.LOAD,
            fallback_countries=["Netherlands", "Germany"],
        ),
    )

    assert len(plan) == 1
    assert plan[0].candidate_locations == [
        "Canada / Montreal",
        "Canada / Vancouver",
        "Netherlands / Amsterdam",
        "Netherlands / Rotterdam",
        "Germany / Berlin",
    ]


def test_create_rotation_plan_filters_services_by_provider_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    proton_env = tmp_path / "proton.env"
    proton_env.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    nord_env = tmp_path / "nord.env"
    nord_env.write_text("VPN_SERVICE_PROVIDER=nordvpn\n")
    manager.add_profile(Profile(name="proton", env_file=str(proton_env)))
    manager.add_profile(Profile(name="nord", env_file=str(nord_env)))
    manager.add_service(
        VPNService.create(
            name="protonvpn-united-kingdom-london",
            port=20000,
            control_port=30000,
            provider="protonvpn",
            profile="proton",
            location="London",
            environment={
                "VPN_SERVICE_PROVIDER": "protonvpn",
                "SERVER_CITIES": "London",
                "SERVER_COUNTRIES": "United Kingdom",
            },
            labels={
                "vpn.type": "vpn",
                "vpn.port": "20000",
                "vpn.control_port": "30000",
                "vpn.provider": "protonvpn",
                "vpn.profile": "proton",
                "vpn.location": "London",
            },
        )
    )
    manager.add_service(
        VPNService.create(
            name="nordvpn-united-kingdom-manchester",
            port=20001,
            control_port=30001,
            provider="nordvpn",
            profile="nord",
            location="Manchester",
            environment={
                "VPN_SERVICE_PROVIDER": "nordvpn",
                "SERVER_CITIES": "Manchester",
                "SERVER_COUNTRIES": "United Kingdom",
            },
            labels={
                "vpn.type": "vpn",
                "vpn.port": "20001",
                "vpn.control_port": "30001",
                "vpn.provider": "nordvpn",
                "vpn.profile": "nord",
                "vpn.location": "Manchester",
            },
        )
    )

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    fleet_manager._sync_services_from_compose()
    monkeypatch.setattr(
        fleet_manager.server_manager,
        "list_countries",
        lambda provider: ["United Kingdom"],
    )
    monkeypatch.setattr(
        fleet_manager.server_manager,
        "list_cities",
        lambda provider, country: {
            "protonvpn": ["London", "Manchester"],
            "nordvpn": ["London", "Manchester"],
        }[provider],
    )

    plan = fleet_manager._create_rotation_plan(
        [
            "protonvpn-united-kingdom-london",
            "nordvpn-united-kingdom-manchester",
        ],
        fleet_state_manager_mod.OperationConfig(
            criteria=fleet_state_manager_mod.RotationCriteria.LOAD,
            provider="nordvpn",
            countries=["United Kingdom"],
        ),
    )

    assert [item.service_name for item in plan] == ["nordvpn-united-kingdom-manchester"]
    assert plan[0].candidate_locations == ["United Kingdom / London"]


def test_rotate_service_preloads_server_catalog_for_async_rotation(
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
    manager.add_service(
        VPNService.create(
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
    )

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    loaded = {}

    async def fake_fetch(verify=True):
        loaded["called"] = True
        fleet_manager.server_manager.data = {
            "protonvpn": {
                "servers": [
                    {"country": "Canada", "city": "Toronto", "ips": ["198.51.100.1"]},
                    {
                        "country": "Canada",
                        "city": "Montreal",
                        "ips": ["198.51.100.2"],
                    },
                    {
                        "country": "Netherlands",
                        "city": "Amsterdam",
                        "ips": ["198.51.100.3"],
                    },
                ]
            }
        }
        return fleet_manager.server_manager.data

    def fail_sync_update(*args, **kwargs):
        raise AssertionError("sync update_servers should not run in async rotation")

    monkeypatch.setattr(
        fleet_manager.server_manager,
        "fetch_server_list_async",
        fake_fetch,
    )
    monkeypatch.setattr(
        fleet_manager.server_manager,
        "update_servers",
        fail_sync_update,
    )

    captured = {}

    async def fake_execute(plan, config):
        captured["candidate_locations"] = plan[0].candidate_locations
        return fleet_state_manager_mod.OperationResult(
            operation_type=fleet_state_manager_mod.OperationType.ROTATE,
            success=True,
            services_affected=[plan[0].service_name],
        )

    monkeypatch.setattr(fleet_manager, "_execute_rotation_plan", fake_execute)

    result = asyncio.run(
        fleet_manager.rotate_service(
            "protonvpn-canada-toronto",
            fleet_state_manager_mod.OperationConfig(
                criteria=fleet_state_manager_mod.RotationCriteria.LOAD,
                fallback_countries=["Netherlands"],
            ),
        )
    )

    assert loaded["called"] is True
    assert result.success is True
    assert captured["candidate_locations"] == [
        "Canada / Montreal",
        "Netherlands / Amsterdam",
    ]


def test_execute_single_rotation_updates_country_and_city_for_cross_country_failover(
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
    manager.add_service(
        VPNService.create(
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
    )

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter([None, "203.0.113.20"])

    async def fake_ip(service_name, timeout=None):
        return next(ip_sequence)

    async def fake_vpn_test(service_name, timeout=3):
        return True

    monkeypatch.setattr(fleet_state_manager_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        fleet_state_manager_mod, "recreate_vpn_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        fleet_state_manager_mod, "start_container", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    target = fleet_state_manager_mod.RotationTarget(
        country="Netherlands",
        city="Amsterdam",
    )
    change = asyncio.run(
        fleet_manager._execute_single_rotation(
            fleet_state_manager_mod.ServiceRotationPlan(
                service_name="protonvpn-canada-toronto",
                old_location="Canada / Toronto",
                new_location="Netherlands / Amsterdam",
                reason="health_check_failed",
                old_target=fleet_state_manager_mod.RotationTarget(
                    country="Canada",
                    city="Toronto",
                ),
                new_target=target,
                candidate_targets=[target],
                candidate_locations=["Netherlands / Amsterdam"],
            )
        )
    )

    updated_service = fleet_manager.compose_manager.get_service(
        "protonvpn-netherlands-amsterdam"
    )
    assert change.final_service_name == "protonvpn-netherlands-amsterdam"
    assert change.old_location == "Canada / Toronto"
    assert change.new_location == "Netherlands / Amsterdam"
    assert updated_service.environment["SERVER_COUNTRIES"] == "Netherlands"
    assert updated_service.environment["SERVER_CITIES"] == "Amsterdam"
    assert updated_service.labels["vpn.country"] == "Netherlands"
    assert updated_service.labels["vpn.location"] == "Amsterdam"


def test_verify_rotation_candidate_rejects_duplicate_healthy_fleet_ip(
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
    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    async def fake_ip(service_name, timeout=None):
        return "203.0.113.20"

    async def fake_vpn_test(service_name, timeout=3):
        return True

    monkeypatch.setattr(fleet_manager, "_check_service_health", fake_check)
    monkeypatch.setattr(fleet_manager, "_get_service_egress_ip", fake_ip)
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    verified, failures, current_ip = asyncio.run(
        fleet_manager._verify_rotation_candidate(
            service_name="candidate-service",
            previous_ip="198.51.100.10",
            config=fleet_state_manager_mod.OperationConfig(
                rotation_verification_attempts=1,
                rotation_verification_delay_seconds=0,
                require_unique_egress_ip=True,
            ),
            reserved_ips={"203.0.113.20"},
        )
    )

    assert verified is False
    assert current_ip == "203.0.113.20"
    assert "healthy fleet" in failures[0]


def test_execute_single_rotation_rolls_back_original_country_after_cross_country_failures(
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
    manager.add_service(
        VPNService.create(
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
    )

    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))
    applied_locations: list[str] = []

    async def fake_sleep(*args, **kwargs):
        return None

    async def fake_check(service_name, timeout=None):
        return service_name, fleet_state_manager_mod.ServiceHealth(
            service_name=service_name,
            is_healthy=True,
            health_score=100,
            last_checked=datetime.now(),
        )

    ip_sequence = iter(["198.51.100.10", "198.51.100.10", "198.51.100.10"])

    async def fake_ip(service_name, timeout=None):
        return next(ip_sequence)

    async def fake_vpn_test(service_name, timeout=3):
        return True

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
    monkeypatch.setattr(
        fleet_state_manager_mod,
        "test_vpn_connection_async",
        fake_vpn_test,
    )

    with pytest.raises(Exception, match="new egress IP"):
        asyncio.run(
            fleet_manager._execute_single_rotation(
                fleet_state_manager_mod.ServiceRotationPlan(
                    service_name="protonvpn-canada-toronto",
                    old_location="Canada / Toronto",
                    new_location="Netherlands / Amsterdam",
                    reason="health_check_failed",
                    old_target=fleet_state_manager_mod.RotationTarget(
                        country="Canada",
                        city="Toronto",
                    ),
                    new_target=fleet_state_manager_mod.RotationTarget(
                        country="Netherlands",
                        city="Amsterdam",
                    ),
                    candidate_targets=[
                        fleet_state_manager_mod.RotationTarget(
                            country="Netherlands",
                            city="Amsterdam",
                        ),
                        fleet_state_manager_mod.RotationTarget(
                            country="Germany",
                            city="Berlin",
                        ),
                    ],
                    candidate_locations=[
                        "Netherlands / Amsterdam",
                        "Germany / Berlin",
                    ],
                )
            )
        )

    restored_service = fleet_manager.compose_manager.get_service(
        "protonvpn-canada-toronto"
    )
    assert restored_service.environment["SERVER_COUNTRIES"] == "Canada"
    assert restored_service.environment["SERVER_CITIES"] == "Toronto"
    assert restored_service.labels["vpn.country"] == "Canada"
    assert restored_service.labels["vpn.location"] == "Toronto"
    assert applied_locations == ["Amsterdam", "Berlin", "Toronto"]


def test_bad_rotation_ip_markers_respect_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    fleet_manager = fleet_state_manager_mod.FleetStateManager(str(compose_path))

    fleet_manager._mark_bad_rotation_ip("203.0.113.20")

    assert fleet_manager._is_bad_rotation_ip("203.0.113.20", cooldown_seconds=3600)

    fleet_manager._clear_bad_rotation_ip("203.0.113.20")

    assert not fleet_manager._is_bad_rotation_ip("203.0.113.20", cooldown_seconds=3600)
