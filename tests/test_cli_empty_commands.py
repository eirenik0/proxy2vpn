import os
import pathlib
import subprocess
import sys

repo_root = pathlib.Path(__file__).resolve().parents[1]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "proxy2vpn", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_root_command_shows_help():
    result = _run_cli()
    assert result.returncode == 0
    assert "proxy2vpn command line interface" in result.stdout


def test_missing_arguments_show_error():
    result = _run_cli("profile", "create")
    assert result.returncode == 2
    assert "Missing parameter" in result.stderr
