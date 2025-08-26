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
from proxy2vpn.adapters import server_manager


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

    profiles = [
        Profile(name="acc1", env_file="env.acc1"),
        Profile(name="acc2", env_file="env.acc2"),
    ]
    for p in profiles:
        p._provider = "prov"
    monkeypatch.setattr(
        fleet_manager.compose_manager, "list_profiles", lambda: profiles
    )

    config = FleetConfig(
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

    profile = Profile(name="acc1", env_file="env.acc1")
    profile._provider = "prov"
    monkeypatch.setattr(
        fleet_manager.compose_manager, "list_profiles", lambda: [profile]
    )

    config = FleetConfig(
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


def test_plan_deployment_unique_ips(fleet_manager, monkeypatch):
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
    profile = Profile(name="acc1", env_file="env.acc1")
    profile._provider = "protonvpn"
    monkeypatch.setattr(
        fleet_manager.compose_manager, "list_profiles", lambda: [profile]
    )

    config = FleetConfig(
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

    profile = Profile(name="acc1", env_file="env.acc1")
    profile._provider = "prov"
    monkeypatch.setattr(
        fleet_manager.compose_manager, "list_profiles", lambda: [profile]
    )

    config = FleetConfig(
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


def test_force_deploy_overwrites_compose(tmp_path, monkeypatch):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)
    manager.add_profile(Profile(name="test", env_file="env.test"))

    fm_initial = FleetManager(compose_file_path=compose_path)
    old_plan = ServicePlan(
        name="oldsvc",
        profile="test",
        location="OldCity",
        country="C",
        port=10000,
        control_port=30000,
        provider="prov",
    )
    old_service = fm_initial._create_service_from_plan(old_plan)
    manager.add_service(old_service)

    fm = FleetManager(compose_file_path=compose_path)
    monkeypatch.setattr(
        "proxy2vpn.adapters.fleet_manager.ensure_network", lambda force: None
    )

    new_plan = [
        ServicePlan(
            name="newsvc",
            profile="test",
            location="NewCity",
            country="C",
            port=10001,
            control_port=30001,
            provider="prov",
        )
    ]

    asyncio.run(fm._create_service_definitions(new_plan, force=True, added_services=[]))

    services = fm.compose_manager.list_services()
    assert [s.name for s in services] == ["newsvc"]


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


def test_multi_provider_fleet_planning(tmp_path):
    """Test multi-provider fleet planning based on profile providers."""
    # Setup compose file and profiles with different providers
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    # Create profile env files with different providers
    expressvpn_env = tmp_path / "expressvpn.env"
    expressvpn_env.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\n"
        "OPENVPN_USER=express_user\n"
        "OPENVPN_PASSWORD=express_pass\n"
    )

    nordvpn_env = tmp_path / "nordvpn.env"
    nordvpn_env.write_text(
        "VPN_SERVICE_PROVIDER=nordvpn\nOPENVPN_USER=nord_user\nOPENVPN_PASSWORD=nord_pass\n"
    )

    protonvpn_env = tmp_path / "protonvpn.env"
    protonvpn_env.write_text(
        "VPN_SERVICE_PROVIDER=protonvpn\n"
        "OPENVPN_USER=proton_user\n"
        "OPENVPN_PASSWORD=proton_pass\n"
    )

    # Add profiles to compose manager
    manager.add_profile(Profile(name="express-acc1", env_file=str(expressvpn_env)))
    manager.add_profile(Profile(name="nord-acc1", env_file=str(nordvpn_env)))
    manager.add_profile(Profile(name="proton-acc1", env_file=str(protonvpn_env)))

    # Create fleet manager and mock city data for each provider
    fleet_manager = FleetManager(compose_file_path=compose_path)

    def fake_list_cities(provider, country):
        provider_cities = {
            "expressvpn": {"Germany": ["Frankfurt"], "France": ["Paris"]},
            "nordvpn": {"Germany": ["Berlin"], "Netherlands": ["Amsterdam"]},
            "protonvpn": {"France": ["Lyon"], "Netherlands": ["Rotterdam"]},
        }
        return provider_cities.get(provider, {}).get(country, [])

    fleet_manager.server_manager.list_cities = fake_list_cities

    # Test multi-provider fleet configuration (no single provider specified)
    config = FleetConfig(
        countries=["Germany", "France", "Netherlands"],
        profiles={"express-acc1": 2, "nord-acc1": 2, "proton-acc1": 2},
        port_start=25000,
    )

    plan = fleet_manager.plan_deployment(config)

    # Verify multi-provider deployment
    assert len(plan.services) == 6  # 2 services per provider
    assert len(plan.providers) == 3  # 3 different providers
    assert "expressvpn" in plan.providers
    assert "nordvpn" in plan.providers
    assert "protonvpn" in plan.providers

    # Verify services are created for each provider
    express_services = [s for s in plan.services if s.provider == "expressvpn"]
    nord_services = [s for s in plan.services if s.provider == "nordvpn"]
    proton_services = [s for s in plan.services if s.provider == "protonvpn"]

    assert len(express_services) == 2
    assert len(nord_services) == 2
    assert len(proton_services) == 2

    # Verify port coordination across providers
    all_ports = [s.port for s in plan.services]
    assert len(set(all_ports)) == 6  # All ports should be unique
    assert min(all_ports) == 25000  # Should start from specified port

    # Verify service naming includes provider
    service_names = [s.name for s in plan.services]
    assert any("expressvpn-" in name for name in service_names)
    assert any("nordvpn-" in name for name in service_names)
    assert any("protonvpn-" in name for name in service_names)


def test_fleet_plan_respects_profile_providers(tmp_path):
    """Ensure fleet planning allocates profiles matching provider."""
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    express_env = tmp_path / "express.env"
    express_env.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=u\nOPENVPN_PASSWORD=p\n"
    )
    nord_env = tmp_path / "nord.env"
    nord_env.write_text(
        "VPN_SERVICE_PROVIDER=nordvpn\nOPENVPN_USER=u\nOPENVPN_PASSWORD=p\n"
    )

    manager.add_profile(Profile(name="express", env_file=str(express_env)))
    manager.add_profile(Profile(name="nord", env_file=str(nord_env)))

    fleet_manager = FleetManager(compose_file_path=compose_path)

    def fake_list_cities(provider, country):
        data = {
            "expressvpn": {"Germany": ["Frankfurt", "Berlin"]},
            "nordvpn": {"Germany": ["Hamburg"]},
        }
        return data.get(provider, {}).get(country, [])

    fleet_manager.server_manager.list_cities = fake_list_cities

    config = FleetConfig(
        countries=["Germany"],
        profiles={"express": 2, "nord": 1},
        port_start=10000,
    )

    plan = fleet_manager.plan_deployment(config)

    express_services = [s for s in plan.services if s.provider == "expressvpn"]
    nord_services = [s for s in plan.services if s.provider == "nordvpn"]

    assert len(express_services) == 2
    assert all(s.profile == "express" for s in express_services)
    assert len(nord_services) == 1
    assert all(s.profile == "nord" for s in nord_services)


def test_profile_validation_during_fleet_planning(tmp_path):
    """Test that fleet planning fails fast when profiles have missing VPN_SERVICE_PROVIDER."""
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)

    # Create invalid profile (missing VPN_SERVICE_PROVIDER)
    invalid_env = tmp_path / "invalid.env"
    invalid_env.write_text(
        "OPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n"
        # Missing VPN_SERVICE_PROVIDER
    )

    # Create valid profile
    valid_env = tmp_path / "valid.env"
    valid_env.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n"
    )

    manager.add_profile(Profile(name="invalid-profile", env_file=str(invalid_env)))
    manager.add_profile(Profile(name="valid-profile", env_file=str(valid_env)))

    fleet_manager = FleetManager(compose_file_path=compose_path)

    # Test fleet planning with invalid profile should fail fast
    config = FleetConfig(
        countries=["Germany"],
        profiles={"invalid-profile": 1, "valid-profile": 1},
    )

    with pytest.raises(
        ValueError, match="Fleet planning failed.*missing VPN_SERVICE_PROVIDER"
    ):
        fleet_manager.plan_deployment(config)


def test_profile_comprehensive_validation(tmp_path, monkeypatch):
    """Test comprehensive profile validation covers all required fields."""

    class DummyServerManager:
        def list_providers(self):
            return ["expressvpn", "nordvpn", "protonvpn"]

    monkeypatch.setattr(server_manager, "ServerManager", lambda: DummyServerManager())

    # Test missing VPN_SERVICE_PROVIDER
    missing_provider_env = tmp_path / "missing_provider.env"
    missing_provider_env.write_text("OPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n")

    profile = Profile(name="test", env_file=str(missing_provider_env))
    errors = profile.validate_env_file()
    assert any("VPN_SERVICE_PROVIDER is required" in error for error in errors)

    # Test missing OPENVPN credentials
    missing_creds_env = tmp_path / "missing_creds.env"
    missing_creds_env.write_text("VPN_SERVICE_PROVIDER=expressvpn\n")

    profile = Profile(name="test", env_file=str(missing_creds_env))
    errors = profile.validate_env_file()
    assert any("OPENVPN_USER is required" in error for error in errors)
    assert any("OPENVPN_PASSWORD is required" in error for error in errors)

    # Test HTTP proxy validation when enabled
    missing_proxy_creds_env = tmp_path / "missing_proxy_creds.env"
    missing_proxy_creds_env.write_text(
        "VPN_SERVICE_PROVIDER=nordvpn\nOPENVPN_USER=user\nOPENVPN_PASSWORD=pass\nHTTPPROXY=on\n"
        # Missing HTTPPROXY_USER and HTTPPROXY_PASSWORD
    )

    profile = Profile(name="test", env_file=str(missing_proxy_creds_env))
    errors = profile.validate_env_file()
    assert any(
        "HTTPPROXY_USER is required when HTTPPROXY=on" in error for error in errors
    )
    assert any(
        "HTTPPROXY_PASSWORD is required when HTTPPROXY=on" in error for error in errors
    )

    # Test invalid VPN provider
    invalid_provider_env = tmp_path / "invalid_provider.env"
    invalid_provider_env.write_text(
        "VPN_SERVICE_PROVIDER=invalidvpn\nOPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n"
    )

    profile = Profile(name="test", env_file=str(invalid_provider_env))
    errors = profile.validate_env_file()
    assert any("Unsupported VPN_SERVICE_PROVIDER" in error for error in errors)

    # Test valid profile passes validation
    valid_env = tmp_path / "valid.env"
    valid_env.write_text(
        "VPN_SERVICE_PROVIDER=protonvpn\n"
        "OPENVPN_USER=user\n"
        "OPENVPN_PASSWORD=pass\n"
        "HTTPPROXY=on\n"
        "HTTPPROXY_USER=proxy_user\n"
        "HTTPPROXY_PASSWORD=proxy_pass\n"
    )

    profile = Profile(name="test", env_file=str(valid_env))
    errors = profile.validate_env_file()
    assert len(errors) == 0  # No validation errors


def test_profile_provider_property_validation(tmp_path):
    """Test that Profile.provider property raises clear errors for missing VPN_SERVICE_PROVIDER."""
    # Test missing VPN_SERVICE_PROVIDER raises ValueError
    missing_env = tmp_path / "missing.env"
    missing_env.write_text("OPENVPN_USER=user\n")

    profile = Profile(name="test", env_file=str(missing_env))

    with pytest.raises(
        ValueError, match="Profile 'test' is missing VPN_SERVICE_PROVIDER"
    ):
        _ = profile.provider

    # Test valid provider returns correctly
    valid_env = tmp_path / "valid.env"
    valid_env.write_text(
        "VPN_SERVICE_PROVIDER=expressvpn\nOPENVPN_USER=user\nOPENVPN_PASSWORD=pass\n"
    )

    profile = Profile(name="test", env_file=str(valid_env))
    assert profile.provider == "expressvpn"
