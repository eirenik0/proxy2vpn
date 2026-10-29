import pytest
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters.compose_manager import ComposeManager
from proxy2vpn.adapters import compose_manager as compose_manager_mod
from proxy2vpn.adapters.compose_validator import validate_compose
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
    assert {s.labels[config.COMPOSE_FILE_LABEL] for s in services} == {
        str(compose_path.resolve())
    }


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
        f"./{config.CONTROL_AUTH_CONFIG_FILE.as_posix()}:/gluetun/auth/config.toml:ro"
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
    assert "127.0.0.1:12345:8888/tcp" in compose_text  # from service
    assert "127.0.0.1:30003:8000/tcp" in compose_text  # from service
    assert (
        f"./{config.CONTROL_AUTH_CONFIG_FILE.as_posix()}:/gluetun/auth/config.toml:ro"
        in compose_text
    )

    # Verify the service can be loaded back correctly
    loaded_service = manager.get_service("vpn1")
    assert loaded_service.port == 12345
    assert loaded_service.profile == "andr"
    assert loaded_service.labels[config.COMPOSE_FILE_LABEL] == str(
        compose_path.resolve()
    )
    auth_config = tmp_path / config.CONTROL_AUTH_CONFIG_FILE
    auth_config.write_text('[[roles]]\nname = "proxy2vpn"\nauth = "none"\n')
    assert validate_compose(compose_path) == []


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


def test_validate_compose_file_validate_locations_flag(tmp_path, monkeypatch):
    compose_path = _copy_compose(tmp_path)
    manager = ComposeManager(compose_path)
    captured = {}

    def fake_validate_compose(path, server_manager=None, validate_locations=False):
        captured["path"] = path
        captured["validate_locations"] = validate_locations
        return []

    monkeypatch.setattr(compose_manager_mod, "validate_compose", fake_validate_compose)
    manager.validate_compose_file()
    assert captured["validate_locations"] is False

    manager.validate_compose_file(validate_locations=True)
    assert captured["validate_locations"] is True


# @lat: [[reliability-tests#Reliability Tests#Compose Stale Writers]]
def test_stale_compose_writer_preserves_newer_profile_and_backup(tmp_path):
    from proxy2vpn.adapters.compose_manager import ComposeConflict

    path = _copy_compose(tmp_path)
    first = ComposeManager(path)
    stale = ComposeManager(path)
    first.add_profile(Profile(name="first", env_file="first.env"))
    current = path.read_bytes()
    backup = path.with_suffix(".yml.bak").read_bytes()

    with pytest.raises(ComposeConflict, match="reload.*retry"):
        stale.add_profile(Profile(name="second", env_file="second.env"))
    assert path.read_bytes() == current
    assert path.with_suffix(".yml.bak").read_bytes() == backup
    assert "first" in {p.name for p in ComposeManager(path).list_profiles()}
    assert "second" not in {p.name for p in ComposeManager(path).list_profiles()}


# @lat: [[reliability-tests#Reliability Tests#Compose Direct Edits And Removal]]
def test_direct_save_rejects_external_edits_and_deleted_file(tmp_path):
    from proxy2vpn.adapters.compose_manager import ComposeConflict

    path = _copy_compose(tmp_path)
    manager = ComposeManager(path)
    changed = path.read_text() + "\n# external edit\n"
    path.write_text(changed)
    manager.data["x-config"]["health_check_interval"] = "99"
    with pytest.raises(ComposeConflict):
        manager.save()
    assert path.read_text() == changed

    manager = ComposeManager(path)
    path.unlink()
    with pytest.raises(ComposeConflict):
        manager.save()
    assert not path.exists()


# @lat: [[reliability-tests#Reliability Tests#Compose Commit Failure]]
def test_failed_compose_commit_can_retry_without_overwriting_other_writer(
    tmp_path, monkeypatch
):
    from proxy2vpn.adapters import compose_manager

    path = _copy_compose(tmp_path)
    manager = ComposeManager(path)
    before = path.read_bytes()
    replace = compose_manager.os.replace
    with monkeypatch.context() as patch:
        patch.setattr(
            compose_manager.os,
            "replace",
            lambda *args: (_ for _ in ()).throw(OSError("interrupted")),
        )
        with pytest.raises(OSError, match="interrupted"):
            manager.add_profile(Profile(name="new", env_file="new.env"))
    assert path.read_bytes() == before
    manager.save()
    assert "new" in {p.name for p in ComposeManager(path).list_profiles()}
    assert compose_manager.os.replace is replace


# @lat: [[reliability-tests#Reliability Tests#Fleet Fresh Compose]]
def test_fleet_operation_reloads_compose_after_another_writer(tmp_path):
    from proxy2vpn.adapters.fleet_state_manager import FleetStateManager

    path = _copy_compose(tmp_path)
    fleet = FleetStateManager(path)
    external = ComposeManager(path)
    external.add_profile(Profile(name="external", env_file="external.env"))
    fleet._sync_services_from_compose()
    fleet.compose_manager.add_profile(Profile(name="next", env_file="next.env"))
    assert {"external", "next"} <= {
        p.name for p in ComposeManager(path).list_profiles()
    }


# @lat: [[reliability-tests#Reliability Tests#Compose Snapshot Bytes]]
@pytest.mark.parametrize("edit_at", ["load", "save"])
def test_compose_fingerprint_tracks_exact_parsed_or_written_bytes(
    tmp_path, monkeypatch, edit_at
):
    from proxy2vpn.adapters import compose_manager
    from proxy2vpn.adapters.compose_manager import ComposeConflict

    path = _copy_compose(tmp_path)
    external = path.read_text() + "\n# newer external edit\n"
    if edit_at == "load":
        validate = compose_manager.validate_compose

        def validate_after_edit(*args, **kwargs):
            path.write_text(external)
            return validate(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(compose_manager, "validate_compose", validate_after_edit)
            manager = ComposeManager(path)
    else:
        manager = ComposeManager(path)
        replace = compose_manager.os.replace

        def replace_then_edit(*args):
            replace(*args)
            path.write_text(external)

        with monkeypatch.context() as patch:
            patch.setattr(compose_manager.os, "replace", replace_then_edit)
            manager.save()

    with pytest.raises(ComposeConflict):
        manager.save()
    assert path.read_text() == external
