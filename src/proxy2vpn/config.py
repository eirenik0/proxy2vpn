"""Default configuration for proxy2vpn.

This module centralizes paths and default values used across the
application.  All state is stored in the docker compose file referenced
by :data:`COMPOSE_FILE`.
"""

from __future__ import annotations

from pathlib import Path

# Path to the docker compose file that acts as the single source of truth
# for all proxy2vpn state.  The path is relative to the current working
# directory of the CLI unless an absolute path is provided by the user.
COMPOSE_FILE: Path = Path("compose.yml")

# Directory used to cache data such as the downloaded server lists.  The
# cache location defaults to ``~/.cache/proxy2vpn`` which follows the
# XDG base directory specification on Linux systems.
CACHE_DIR: Path = Path.home() / ".cache" / "proxy2vpn"

# Default VPN provider used when creating new services if none is
# explicitly specified by the user.
DEFAULT_PROVIDER = "protonvpn"

# Starting port used when automatically allocating ports for new VPN
# services.  The manager will search for the next free port starting from
# this value.
DEFAULT_PORT_START = 20000

# URL of the gluetun server list JSON file.  This file is fetched and
# cached by :class:`ServerManager` to provide location validation and
# listing of available servers.
SERVER_LIST_URL = "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"
