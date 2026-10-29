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


# @lat: [[reliability-tests#Reliability Tests#Forced Deployment Shared Network]]
def test_forced_definitions_preserve_network_and_other_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from proxy2vpn.adapters import docker_ops
    from proxy2vpn.adapters.fleet_manager import ServicePlan
    from proxy2vpn.core import config

    path = workspace(tmp_path)
    other = tmp_path / "other.yml"
    ComposeManager.create_initial_compose(other)
    before = other.read_bytes()
    attached = [
        SimpleNamespace(name="own", labels={config.COMPOSE_FILE_LABEL: str(path)}),
        SimpleNamespace(name="foreign", labels={config.COMPOSE_FILE_LABEL: str(other)}),
        SimpleNamespace(name="unmanaged", labels={}),
    ]
    effects = []

    class Network:
        containers = attached

        def reload(self):
            pass

        def disconnect(self, container, force=False):
            effects.append(("disconnect", container.name))

        def remove(self):
            effects.append(("remove",))

    network = Network()
    client = SimpleNamespace(
        networks=SimpleNamespace(
            list=lambda **kwargs: [network],
            create=lambda **kwargs: effects.append(("create",)),
        )
    )
    monkeypatch.setattr(docker_ops, "_client", lambda: client)
    manager = FleetManager(path)
    plan = ServicePlan(
        name="new",
        profile="account",
        location="Berlin",
        country="Germany",
        port=20000,
        control_port=30000,
        provider="protonvpn",
    )
    added = []
    asyncio.run(manager._create_service_definitions([plan], True, added))
    assert added == ["new"]
    assert [service.name for service in ComposeManager(path).list_services()] == ["new"]
    assert effects == []
    assert network.containers == attached
    assert other.read_bytes() == before


# @lat: [[reliability-tests#Reliability Tests#Scale Rejected Definition]]
def test_scale_rejected_name_preserves_existing_definition(tmp_path, monkeypatch):
    from proxy2vpn.core.models import VPNService

    path = workspace(tmp_path)
    compose = ComposeManager(path)
    name = "protonvpn-germany-berlin-20000"
    existing = VPNService.create(
        name=name,
        port=20010,
        control_port=30010,
        provider="protonvpn",
        profile="account",
        location="Berlin",
        environment={},
        labels={},
    )
    compose.add_service(existing)
    before = path.read_bytes()
    manager = fleet_state.FleetStateManager(path)
    monkeypatch.setattr(manager.server_manager, "list_cities", lambda *args: ["Berlin"])
    monkeypatch.setattr(fleet_state, "ensure_network", lambda *args: None)

    async def loaded():
        pass

    monkeypatch.setattr(manager, "_ensure_server_catalog_loaded", loaded)

    async def run():
        try:
            return await manager.scale_fleet(
                fleet_state.OperationConfig(countries=["Germany"]),
                fleet_state.OperationType.SCALE_UP,
            )
        finally:
            await manager.close()

    result = asyncio.run(run())
    assert not result.success
    assert path.read_bytes() == before
    assert ComposeManager(path).get_service(name).port == 20010
