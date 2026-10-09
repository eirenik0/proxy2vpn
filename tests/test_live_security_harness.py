"""Credential-free guard checks for the opt-in live harness."""

from types import SimpleNamespace

import pytest
import test_live_security as live


# @lat: [[live-security-tests#Live Security Tests#Harness Failure And Isolation Guards]]
def test_live_harness_preserves_backups_and_attempts_all_cleanup(tmp_path, monkeypatch):
    ports = iter((28000, 28001))
    monkeypatch.setattr(live, "free_port", lambda: next(ports))
    profile = tmp_path / "input.env"
    profile.write_text(
        "VPN_SERVICE_PROVIDER=protonvpn\nOPENVPN_USER=test\nOPENVPN_PASSWORD=test\n"
    )
    lab = live.Lab(tmp_path / "lab", profile, "192.168.1.20")
    server = lab.root / live.AUTH_NAMES[0]
    live.private_write(
        server, '[[roles]]\nname="legacy"\nauth="none"\nroutes=["GET /v1/vpn/status"]\n'
    )
    before = lab.compose.read_bytes()
    lab.secure("127.0.0.1", replace=True)
    assert lab.compose.with_name("compose.yml.bak").read_bytes() == before
    assert live.security.load_control_auth(lab.compose, "monitor")
    first = server.read_bytes()
    lab.secure("127.0.0.1", replace=True)
    assert server.read_bytes() == first
    assert lab.compose.with_name("compose.yml.bak").read_bytes() == before
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if "down" in args:
            pytest.fail("injected Compose failure", pytrace=False)
        if args[1:3] == ["ps", "-aq"]:
            return "owned-container\n"
        if args[1:3] == ["network", "ls"]:
            return "owned-network\n"
        return ""

    monkeypatch.setattr(live, "command", command)
    with pytest.raises(pytest.fail.Exception, match="cleanup failed"):
        lab.close()
    assert any(args[1:3] == ["rm", "-f"] for args in calls)
    assert any(args[1:3] == ["network", "rm"] for args in calls)


# @lat: [[live-security-tests#Live Security Tests#Strict Prerequisites And Private Failures]]
def test_live_strict_prerequisite_fails_and_subprocess_output_is_private(monkeypatch):
    monkeypatch.setenv("PROXY2VPN_LIVE_STRICT", "1")
    with pytest.raises(pytest.fail.Exception, match="Required live prerequisite unmet"):
        live.prerequisite("credentials")
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stdout="sensitive", stderr="sensitive"
        ),
    )
    with pytest.raises(pytest.fail.Exception) as error:
        live.command(["docker", "compose"], stage="test stage")
    assert str(error.value) == "Live operation failed: test stage"


# @lat: [[live-security-tests#Live Security Tests#Cleanup Failures Erase Private Inputs]]
@pytest.mark.parametrize("delete_fails", [False, True])
def test_cleanup_failure_attempts_every_lab_and_erases_private_inputs(
    tmp_path, monkeypatch, delete_fails
):
    root = tmp_path / "private"
    root.mkdir()
    (root / "vpn.env").write_text("private-input")
    attempted = []

    def close(project, fail):
        attempted.append(project)
        if fail:
            pytest.fail("injected resource failure", pytrace=False)

    labs = [
        SimpleNamespace(project="second", close=lambda: close("second", False)),
        SimpleNamespace(project="first", close=lambda: close("first", True)),
    ]
    if delete_fails:

        def refuse_delete(path):
            raise OSError("sensitive OS error")

        monkeypatch.setattr(live.shutil, "rmtree", refuse_delete)
    with pytest.raises(pytest.fail.Exception) as error:
        live.cleanup_labs(labs, root)
    assert attempted == ["first", "second"]
    assert "resources for first" in str(error.value)
    assert "sensitive" not in str(error.value)
    if delete_fails:
        assert "private scratch cleanup failed" in str(error.value)
    else:
        assert not root.exists()


# @lat: [[live-security-tests#Live Security Tests#Independent Cleanup Stages]]
@pytest.mark.parametrize(
    "failed_stage",
    [
        "find owned test containers",
        "remove owned test containers",
        "find owned Compose networks",
        "find owned probe bridges",
    ],
)
def test_cleanup_continues_after_each_discovery_or_removal_failure(
    tmp_path, monkeypatch, failed_stage
):
    ports = iter((28000, 28001))
    monkeypatch.setattr(live, "free_port", lambda: next(ports))
    profile = tmp_path / "vpn.env"
    profile.write_text("VPN_SERVICE_PROVIDER=protonvpn\n")
    lab = live.Lab(tmp_path / "lab", profile, "192.168.1.20")
    stages = []

    def command(args, *, stage, **kwargs):
        stages.append(stage)
        if stage == failed_stage:
            pytest.fail("injected discovery/removal failure", pytrace=False)
        if stage == "find owned test containers":
            return "owned-container"
        if stage == "find owned Compose networks":
            return "compose-network"
        if stage == "find owned probe bridges":
            return "probe-network"
        return ""

    monkeypatch.setattr(live, "command", command)
    with pytest.raises(pytest.fail.Exception, match=failed_stage):
        lab.close()
    assert "find owned Compose networks" in stages
    assert "find owned probe bridges" in stages
    assert "remove isolated network" in stages
