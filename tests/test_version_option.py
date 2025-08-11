import os
import pathlib
import subprocess
import sys

repo_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))
import proxy2vpn  # noqa


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-m", "proxy2vpn", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_version_flag_long():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == proxy2vpn.__version__


def test_version_flag_short():
    result = _run_cli("-V")
    assert result.returncode == 0
    assert result.stdout.strip() == proxy2vpn.__version__
