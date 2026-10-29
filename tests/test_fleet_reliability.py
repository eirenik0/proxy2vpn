"""Fleet resources are reserved before runtime effects, using isolated roots."""

import asyncio

import pytest

from proxy2vpn.adapters import fleet_state_manager as fleet_state
from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters.fleet_manager import FleetConfig, FleetManager
from proxy2vpn.core.models import Profile


def workspace(tmp_path):
    path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(path)
    env = tmp_path / "account.env"
    env.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    ComposeManager(path).add_profile(Profile(name="account", env_file=str(env)))
    return path


# @lat: [[reliability-tests#Reliability Tests#Scale Batch Reservations]]
def test_scale_batch_reserves_distinct_ports_before_start(tmp_path, monkeypatch):
    path = workspace(tmp_path)
    manager = fleet_state.FleetStateManager(path)
    monkeypatch.setattr(manager.server_manager, "list_cities", lambda *args: ["Berlin"])
    started = []
    monkeypatch.setattr(fleet_state, "ensure_network", lambda *args: None)
    monkeypatch.setattr(
        fleet_state, "start_vpn_service", lambda service, *args: started.append(service)
    )

    async def run():
        async def loaded():
            pass

        monkeypatch.setattr(manager, "_ensure_server_catalog_loaded", loaded)
        try:
            return await manager.scale_fleet(
                fleet_state.OperationConfig(countries=["Germany"]),
                fleet_state.OperationType.SCALE_UP,
                3,
            )
        finally:
            await manager.close()

    result = asyncio.run(run())
    assert result.success, result.errors
    services = ComposeManager(path).list_services()
    assert len(services) == len(started) == len(result.services_affected) == 3
    ports = [
        port for service in services for port in (service.port, service.control_port)
    ]
    assert len(set(ports)) == 6


# @lat: [[reliability-tests#Reliability Tests#Plan Shared Host Ports]]
def test_plan_reserves_shared_namespace_and_existing_bindings(tmp_path, monkeypatch):
    path = workspace(tmp_path)
    compose = ComposeManager(path)
    compose.data["services"]["other"] = {"ports": ["127.0.0.1:30000:80"]}
    compose.save()
    manager = FleetManager(path)
    monkeypatch.setattr(
        manager.server_manager, "list_cities", lambda *args: ["Berlin", "Munich"]
    )
    # This unmanaged mapping is only relevant to occupied ports, not VPN identity.
    monkeypatch.setattr(manager.compose_manager, "list_services", lambda: [])
    plan = manager.plan_deployment(
        FleetConfig(countries=["Germany"], profiles={"account": 2}, port_start=30000)
    )
    ports = [
        port
        for service in plan.services
        for port in (service.port, service.control_port)
    ]
    assert 30000 not in ports
    assert len(set(ports)) == 4


# @lat: [[reliability-tests#Reliability Tests#Port Exhaustion]]
def test_exhausted_scale_batch_does_not_reserve_partial_ports(tmp_path, monkeypatch):
    manager = fleet_state.FleetStateManager(workspace(tmp_path))
    occupied = set(range(1, 65535))
    monkeypatch.setattr(manager.compose_manager, "get_all_used_ports", lambda: occupied)
    manager.allocated_ports = occupied.copy()
    with pytest.raises(ValueError, match="port"):
        manager._allocate_ports(1)
    assert manager.allocated_ports == occupied
