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

# Starting port used when allocating host ports for the control API.
# Control ports are bound to localhost and kept separate from proxy ports.
DEFAULT_CONTROL_PORT_START = 30000

# URL of the gluetun server list JSON file.  This file is fetched and
# cached by :class:`ServerManager` to provide location validation and
# listing of available servers.
SERVER_LIST_URL = "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"

# Default timeout (seconds) for HTTP requests to the control API.
DEFAULT_TIMEOUT = 10

# Maximum number of retry attempts for HTTP requests.
MAX_RETRIES = 3

# Whether to verify SSL certificates for HTTP requests.
VERIFY_SSL = True

# Mapping of control API endpoints.
CONTROL_API_ENDPOINTS = {
    "status": "/status",
    "openvpn": "/openvpn",
    "ip": "/ip",
    "openvpn_status": "/openvpn/status",
}

# Path to the control server authentication configuration mounted into
# each Gluetun container.  The configuration disables authentication for
# a small set of non-sensitive routes used by proxy2vpn so the control
# API can be queried without manual setup.
CONTROL_AUTH_CONFIG_FILE: Path = Path("control-server-auth.toml")

# Default content of the control server authentication configuration.
# It declares a single role allowing access to the endpoints required by
# proxy2vpn with ``auth = "none"`` so no credentials are needed.
CONTROL_AUTH_CONFIG_TEMPLATE = """[[roles]]
name = "proxy2vpn"
auth = "none"
routes = [
  "GET /v1/status",
  "GET /v1/ip",
  "POST /v1/openvpn",
  "PUT /v1/openvpn/status",
]
"""
