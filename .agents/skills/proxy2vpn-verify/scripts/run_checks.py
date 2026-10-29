#!/usr/bin/env python3
"""Run isolated regression scopes from the repository's locked environment."""

import argparse
from pathlib import Path
import shlex
import subprocess


SUITES = {
    "cli": (
        "test_cli_entrypoint.py",
        "test_cli_empty_commands.py",
        "test_cli_regression_matrix.py",
        "test_agent_cli.py",
        "test_fleet_cli.py",
    ),
    "recovery": ("test_recovery_policy.py", "test_agent_runtime.py"),
    "storage": (
        "test_agent_storage.py",
        "test_agent_evidence.py",
        "test_incident_retention.py",
    ),
    "runtime": (
        "test_gluetun_runtime.py",
        "test_gluetun_control_client.py",
        "test_docker_ops.py",
    ),
    "egress": (
        "test_external_proxy.py",
        "test_health_assessment.py",
        "test_diagnostics.py",
        "test_iproyal_adapter.py",
        "test_mobile_operations.py",
    ),
    "fleet": (
        "test_fleet_manager.py",
        "test_fleet_state_manager.py",
        "test_fleet_allocation_table.py",
        "test_fleet_cli.py",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scopes", nargs="+", choices=[*SUITES, "full"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    if "full" in args.scopes:
        commands = [["make", "test"]]
    else:
        selected = dict.fromkeys(
            f"tests/{name}" for scope in args.scopes for name in SUITES[scope]
        )
        for name in selected:
            if not (root / name).is_file():
                parser.error(f"Regression file no longer exists: {name}")
        commands = [["uv", "run", "--locked", "pytest", *selected, "-q"]]
    commands.append(["lat", "check"])
    for command in commands:
        print(f"{root}: {shlex.join(command)}", flush=True)
        if not args.dry_run:
            try:
                result = subprocess.run(command, cwd=root, check=False)
            except FileNotFoundError:
                parser.exit(127, f"Required executable not found: {command[0]}\n")
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
