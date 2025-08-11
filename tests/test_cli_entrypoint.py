import os
import pathlib
import subprocess
import sys


def test_module_entrypoint():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    result = subprocess.run(
        [sys.executable, "-m", "proxy2vpn", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "proxy2vpn command line interface" in result.stdout
