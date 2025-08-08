#!/usr/bin/env python3
"""Simple installer for proxy2vpn.

This script mirrors the behaviour of the existing ``install.sh`` script but is
implemented in Python so it can be executed on systems where running a shell
script is inconvenient. It downloads the required project files into the
``~/.proxy2vpn`` directory and creates a ``proxy2vpn`` wrapper in
``~/.local/bin`` so the tool can be executed from anywhere on the system.

The steps performed are intentionally similar to the bash installer but kept a
little simpler:
    * ensure required commands are available
    * create installation directories
    * download proxy2vpn resources from the GitHub repository
    * write a small wrapper script in ``~/.local/bin``
    * (optionally) add ``~/.local/bin`` to the user's PATH

The script is idempotent – running it multiple times will overwrite existing
files with the latest versions from the repository.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path
from urllib.request import urlopen

# Base URL for raw files in the repository
REPO_RAW_URL = "https://raw.githubusercontent.com/eirenik0/proxy2vpn/main"

HOME = Path.home()
USER_BIN = HOME / ".local" / "bin"
INSTALL_DIR = HOME / ".proxy2vpn"
SCRIPT_PATH = USER_BIN / "proxy2vpn"


class Colours:
    """ANSI colour escape sequences used for status output."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"  # No colour


# ---------------------------------------------------------------------------
# Helper output functions
# ---------------------------------------------------------------------------

def print_error(msg: str) -> None:
    print(f"{Colours.RED}Error: {msg}{Colours.NC}", file=sys.stderr)


def print_success(msg: str) -> None:
    print(f"{Colours.GREEN}{msg}{Colours.NC}")


def print_info(msg: str) -> None:
    print(f"{Colours.YELLOW}{msg}{Colours.NC}")


def print_header(msg: str) -> None:
    print(f"{Colours.BLUE}{msg}{Colours.NC}")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def command_exists(command: str) -> bool:
    """Return True if a command exists on the path."""

    return shutil.which(command) is not None


def download_text(url: str) -> str:
    """Download text content from *url* and return it."""

    with urlopen(url) as resp:  # nosec - source is hardcoded
        return resp.read().decode("utf-8")


def download_file(url: str, destination: Path) -> None:
    """Download a file from *url* to *destination*."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as resp, destination.open("wb") as fh:  # nosec - URL fixed
        shutil.copyfileobj(resp, fh)


# ---------------------------------------------------------------------------
# Installation steps
# ---------------------------------------------------------------------------

def ensure_requirements() -> None:
    """Check for required external commands."""

    if not command_exists("docker"):
        print_error("Docker is not installed. Please install Docker to use proxy2vpn.")
        sys.exit(1)

    if not command_exists("jq"):
        print_info("jq not found – some proxy2vpn features may be unavailable.")


def create_directories() -> None:
    """Create installation directories."""

    for path in [USER_BIN, INSTALL_DIR, INSTALL_DIR / "profiles", INSTALL_DIR / "presets", INSTALL_DIR / "scripts"]:
        path.mkdir(parents=True, exist_ok=True)


def install_main_script() -> None:
    """Download and install the main shell script."""

    print_info("Downloading proxy2vpn shell script...")
    content = download_text(f"{REPO_RAW_URL}/proxy2vpn.sh")

    # Replace dynamic SCRIPT_DIR detection with static installation path
    content = re.sub(
        r"readlink_result=.*\nSCRIPT_DIR=\".*\"",
        f'SCRIPT_DIR="{INSTALL_DIR}"',
        content,
    )

    script_file = INSTALL_DIR / "proxy2vpn.sh"
    script_file.write_text(content)
    script_file.chmod(script_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_support_files() -> None:
    """Download config, presets and helper scripts."""

    print_info("Downloading configuration and preset files...")
    download_file(f"{REPO_RAW_URL}/config.json", INSTALL_DIR / "config.json")
    download_file(f"{REPO_RAW_URL}/presets/presets.json", INSTALL_DIR / "presets/presets.json")

    print_info("Downloading helper scripts...")
    for script in ["server_utils.sh", "server_display.sh"]:
        dest = INSTALL_DIR / "scripts" / script
        download_file(f"{REPO_RAW_URL}/scripts/{script}", dest)
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    profile_readme = INSTALL_DIR / "profiles" / "README.md"
    profile_readme.write_text(
        "# User Profiles\n\n"
        "This directory contains user profiles for proxy2vpn.\n"
        "Each profile is stored as an .env file with VPN credentials.\n\n"
        "To create a profile, use:\n"
        "```bash\n"
        "proxy2vpn create-profile myprofile username password\n"
        "```\n"
    )


def create_wrapper() -> None:
    """Create the executable wrapper placed in ~/.local/bin."""

    print_info("Creating wrapper script in ~/.local/bin...")
    wrapper = SCRIPT_PATH
    wrapper.write_text(
        "#!/bin/bash\n"
        f"exec \"{INSTALL_DIR}/proxy2vpn.sh\" \"$@\"\n"
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if str(USER_BIN) not in os.environ.get("PATH", "").split(":"):
        shell = os.environ.get("SHELL", "")
        if "bash" in shell:
            config = HOME / ".bashrc"
        elif "zsh" in shell:
            config = HOME / ".zshrc"
        else:
            config = None

        if config:
            with config.open("a") as fh:
                fh.write(f"\nexport PATH=\"{USER_BIN}:$PATH\"\n")
            print_info(f"Added {USER_BIN} to {config}. Please restart your shell or source the file.")
        else:
            print_info(f"Please add {USER_BIN} to your PATH manually.")


def verify_installation() -> None:
    """Perform basic verification of the installation."""

    print_info("Verifying installation...")
    issues = []

    if not (INSTALL_DIR / "proxy2vpn.sh").exists():
        issues.append("Main script missing")
    if not SCRIPT_PATH.exists():
        issues.append("Wrapper script missing")
    if issues:
        for issue in issues:
            print_error(issue)
        print_error("Installation completed with issues.")
        sys.exit(1)

    print_success("proxy2vpn has been installed successfully!")


def main() -> None:
    print_header("proxy2vpn Python installer")
    ensure_requirements()
    create_directories()
    install_main_script()
    install_support_files()
    create_wrapper()
    verify_installation()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_error("Installation cancelled by user")
        sys.exit(130)
