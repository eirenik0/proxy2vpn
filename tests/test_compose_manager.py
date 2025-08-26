import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.core import config
from proxy2vpn.core.models import Profile, VPNService


def _copy_compose(tmp_path):
    compose_src = pathlib.Path(__file__).parent / "test_compose.yml"
    env_path = tmp_path / "env.test"
    env_path.write_text("KEY=value\n")
    compose_path = tmp_path / "docker-compose.yml"
    text = compose_src.read_text().replace("env.test", str(env_path))
    compose_path.write_text(text)
    return compose_path


def test_read_config_and_services(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    assert manager.config["health_check_interval"] == "5"
    services = manager.list_services()
    assert {s.name for s in services} == {"testvpn1", "testvpn2"}


def test_profile_management(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    profiles = {p.name for p in manager.list_profiles()}
    assert profiles == {"test"}
    new_profile = Profile(name="new", env_file="env.new")
    manager.add_profile(new_profile)
    assert "new" in {p.name for p in manager.list_profiles()}
    manager.remove_profile("new")
    assert "new" not in {p.name for p in manager.list_profiles()}


def test_profile_inserted_before_services(tmp_path):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)
    env_path = tmp_path / "env.top"
    env_path.write_text("KEY=value\n")
    manager.add_profile(Profile(name="top", env_file=str(env_path)))
    content = compose_path.read_text()
    assert content.index("x-vpn-base-top:") < content.index("services:")


def test_add_and_remove_service(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    new_service = VPNService.create(
        name="vpn3",
        port=7777,
        control_port=30002,
        provider="protonvpn",
        profile="test",
        location="LA",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "LA",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "8888",
            "vpn.control_port": "30002",
            "vpn.provider": "protonvpn",
            "vpn.profile": "test",
            "vpn.location": "LA",
        },
    )
    manager.add_service(new_service)
    assert "vpn3" in {s.name for s in manager.list_services()}
    # ensure new service uses the profile anchor
    compose_text = compose_path.read_text()
    assert "vpn3:" in compose_text
    assert "<<: *vpn-base-test" in compose_text
    assert (
        f"{config.CONTROL_AUTH_CONFIG_FILE}:/gluetun/auth/config.toml:ro"
        in compose_text
    )
    manager.remove_service("vpn3")
    assert "vpn3" not in {s.name for s in manager.list_services()}


def test_add_service_after_init(tmp_path):
    compose_path = tmp_path / "compose.yml"
    ComposeManager.create_initial_compose(compose_path, force=True)
    manager = ComposeManager(compose_path)
    env_path = tmp_path / "env.andr"
    env_path.write_text("KEY=value\n")
    profile = Profile(name="andr", env_file=str(env_path))
    manager.add_profile(profile)
    service = VPNService.create(
        name="vpn1",
        port=12345,
        control_port=30003,
        provider="protonvpn",
        profile="andr",
        location="LA",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "LA",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "12345",
            "vpn.control_port": "30003",
            "vpn.provider": "protonvpn",
            "vpn.profile": "andr",
            "vpn.location": "LA",
        },
    )
    manager.add_service(service)
    compose_text = compose_path.read_text()
    # Check that service contains the profile configuration (merged)
    assert "image: qmcgaw/gluetun" in compose_text  # from profile
    assert "0.0.0.0:12345:8888/tcp" in compose_text  # from service
    assert "127.0.0.1:30003:8000/tcp" in compose_text  # from service
    assert (
        f"{config.CONTROL_AUTH_CONFIG_FILE}:/gluetun/auth/config.toml:ro"
        in compose_text
    )

    # Verify the service can be loaded back correctly
    loaded_service = manager.get_service("vpn1")
    assert loaded_service.port == 12345
    assert loaded_service.profile == "andr"


def test_no_duplicate_profile_anchors(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)

    env_path = tmp_path / "env.dup"
    env_path.write_text("KEY=value\n")
    profile = Profile(name="dup", env_file=str(env_path))
    manager.add_profile(profile)

    svc1 = VPNService.create(
        name="vpnA",
        port=1111,
        control_port=30002,
        provider="protonvpn",
        profile="dup",
        location="LA",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "LA",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "1111",
            "vpn.control_port": "30002",
            "vpn.provider": "protonvpn",
            "vpn.profile": "dup",
            "vpn.location": "LA",
        },
    )
    svc2 = VPNService.create(
        name="vpnB",
        port=1112,
        control_port=30003,
        provider="protonvpn",
        profile="dup",
        location="SF",
        environment={
            "VPN_SERVICE_PROVIDER": "protonvpn",
            "SERVER_CITIES": "SF",
        },
        labels={
            "vpn.type": "vpn",
            "vpn.port": "1112",
            "vpn.control_port": "30003",
            "vpn.provider": "protonvpn",
            "vpn.profile": "dup",
            "vpn.location": "SF",
        },
    )
    manager.add_service(svc1)
    manager.add_service(svc2)

    compose_text = compose_path.read_text()
    assert compose_text.count("&vpn-base-dup") == 1
    assert compose_text.count("<<: &vpn-base-dup") <= 1

    from ruamel.yaml import YAML
    from ruamel.yaml.composer import ReusedAnchorWarning
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", ReusedAnchorWarning)
        YAML().load(compose_text)
        assert not any(isinstance(m.message, ReusedAnchorWarning) for m in w)


def test_recover_from_corruption(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    manager.save()
    compose_path.write_text("not: [valid")
    recovered = ComposeManager(compose_path)
    assert recovered.config["health_check_interval"] == "5"
