import os
import pathlib
import subprocess
import sys

from ruamel.yaml import YAML


def _run_proxy2vpn(args, cwd):
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "proxy2vpn", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def test_init_creates_compose(tmp_path):
    result = _run_proxy2vpn(["init"], tmp_path)
    assert result.returncode == 0
    compose = tmp_path / "compose.yml"
    assert compose.exists()
    yaml = YAML()
    data = yaml.load(compose.read_text())
    assert data["services"] == {}
    assert "proxy2vpn_network" in data["networks"]


def test_init_requires_force(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")

    result = _run_proxy2vpn(["init"], tmp_path)
    assert result.returncode != 0

    result = _run_proxy2vpn(["init", "--force"], tmp_path)
    assert result.returncode == 0
