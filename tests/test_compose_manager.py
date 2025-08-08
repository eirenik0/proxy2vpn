import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.compose_manager import ComposeManager
from proxy2vpn.models import Profile, VPNService


def _copy_compose(tmp_path):
    compose_src = pathlib.Path(__file__).parent / "test_compose.yml"
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(compose_src.read_text())
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


def test_add_and_remove_service(tmp_path):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    new_service = VPNService(
        name="vpn3",
        port=7777,
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
    manager.remove_service("vpn3")
    assert "vpn3" not in {s.name for s in manager.list_services()}
