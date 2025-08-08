#!/usr/bin/env python3
"""Installer for the Python-based proxy2vpn CLI.

This script installs the proxy2vpn Python package using ``pip`` so that the
project can be used on systems without Bash.  It verifies that the required
external dependencies are available and adds ``~/.local/bin`` to the user's
``PATH`` if necessary.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_SPEC = "git+https://github.com/eirenik0/proxy2vpn.git"
USER_BIN = Path.home() / ".local" / "bin"


class Colours:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"


def print_error(msg: str) -> None:
    print(f"{Colours.RED}Error: {msg}{Colours.NC}", file=sys.stderr)


def print_success(msg: str) -> None:
    print(f"{Colours.GREEN}{msg}{Colours.NC}")


def print_info(msg: str) -> None:
    print(f"{Colours.YELLOW}{msg}{Colours.NC}")


def print_header(msg: str) -> None:
    print(f"{Colours.BLUE}{msg}{Colours.NC}")


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def ensure_requirements() -> None:
    if sys.version_info < (3, 10):
        print_error("Python 3.10 or newer is required.")
        sys.exit(1)
    if not command_exists("docker"):
        print_error("Docker is not installed. Please install Docker to use proxy2vpn.")
        sys.exit(1)


def install_package() -> None:
    print_info("Installing proxy2vpn Python package...")
    cmd = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", PACKAGE_SPEC]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print_error(f"pip failed with exit code {exc.returncode}")
        sys.exit(exc.returncode)


def ensure_user_bin_on_path() -> None:
    if str(USER_BIN) in os.environ.get("PATH", "").split(":"):
        return
    shell = os.environ.get("SHELL", "")
    if "bash" in shell:
        rc = Path.home() / ".bashrc"
    elif "zsh" in shell:
        rc = Path.home() / ".zshrc"
    else:
        rc = None
    if rc and rc.exists():
        with rc.open("a") as fh:
            fh.write(f"\nexport PATH=\"{USER_BIN}:$PATH\"\n")
        print_info(f"Added {USER_BIN} to {rc}. Please restart your shell or source the file.")
    else:
        print_info(f"Please add {USER_BIN} to your PATH to use proxy2vpn.")


def verify_installation() -> None:
    if shutil.which("proxy2vpn"):
        print_success("proxy2vpn has been installed successfully!")
    else:
        print_error("proxy2vpn command not found on PATH after installation.")
        sys.exit(1)


def main() -> None:
    print_header("proxy2vpn Python installer")
    ensure_requirements()
    install_package()
    ensure_user_bin_on_path()
    verify_installation()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_error("Installation cancelled by user")
        sys.exit(130)
