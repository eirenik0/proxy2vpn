"""Default configuration for proxy2vpn.

This module centralizes paths and default values used across the
application.  All state is stored in the docker compose file referenced
by :data:`COMPOSE_FILE`.
"""

from __future__ import annotations

import os
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
    # Keep keys stable for call sites/tests; update paths to v1 routes
    "status": "/v1/openvpn/status",
    "openvpn": "/v1/openvpn/status",  # legacy key pointing to status path
    "ip": "/v1/publicip/ip",
    "openvpn_status": "/v1/openvpn/status",
    "dns_status": "/v1/dns/status",
    "updater_status": "/v1/updater/status",
    "port_forward": "/v1/openvpn/portforwarded",
}

# Path to the control server authentication configuration mounted into
# each Gluetun container.  The configuration disables authentication for
# a small set of non-sensitive routes used by proxy2vpn so the control
# API can be queried without manual setup.
CONTROL_AUTH_CONFIG_FILE: Path = Path("control-server-auth.toml")

# Agent state lives next to the compose file so watchdog state stays scoped
# to one workspace/compose root.
AGENT_STATE_DIRNAME = ".proxy2vpn-agent"
AGENT_STATE_FILE = "state.json"
AGENT_INCIDENTS_FILE = "incidents.jsonl"
AGENT_RUNTIME_LOCK_FILE = "runtime.lock"
AGENT_DEFAULT_INTERVAL = 30
AGENT_HEALTH_THRESHOLD = 60
AGENT_RECHECK_DELAY_SECONDS = 15
AGENT_RESTORE_COOLDOWN_SECONDS = 600
AGENT_INCIDENT_COOLDOWN_SECONDS = 1800
AGENT_ACTION_HISTORY_LIMIT = 100
AGENT_OPENAI_MODEL = "gpt-5-nano"
AGENT_OPENAI_TIMEOUT_SECONDS = 10
AGENT_OPENAI_MAX_OUTPUT_TOKENS = 220
AGENT_OPENAI_REASONING_EFFORT = "minimal"

# Default content of the control server authentication configuration.
# It declares a single role allowing access to the endpoints required by
# proxy2vpn with ``auth = "none"`` so no credentials are needed.
CONTROL_AUTH_CONFIG_TEMPLATE = """[[roles]]
name = "proxy2vpn"
auth = "none"
routes = [
  # OpenVPN status and settings
  "GET /v1/openvpn/status",
  "PUT /v1/openvpn/status",
  "GET /v1/openvpn/portforwarded",
  "GET /v1/openvpn/settings",

  # DNS control
  "GET /v1/dns/status",
  "PUT /v1/dns/status",

  # Updater control
  "GET /v1/updater/status",
  "PUT /v1/updater/status",

  # Public IP
  "GET /v1/publicip/ip",
]
"""


def resolve_compose_root(compose_file: Path | None = None) -> Path:
    """Return the resolved directory that owns compose-managed state."""
    target = (compose_file or COMPOSE_FILE).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    else:
        target = target.resolve()
    return target.parent


def resolve_control_auth_config(
    compose_file: Path | None = None, compose_root: Path | None = None
) -> Path:
    """Return the resolved auth config path for the active compose file."""
    if compose_root is not None:
        return compose_root.expanduser().resolve() / CONTROL_AUTH_CONFIG_FILE
    return resolve_compose_root(compose_file) / CONTROL_AUTH_CONFIG_FILE


def relativize_path_for_compose(
    path: Path, compose_file: Path | None = None, cwd: Path | None = None
) -> str:
    """Return PATH relative to the compose root.

    Relative user input is first resolved against ``cwd`` (default: current
    working directory) so CLI invocations behave like normal shell paths.
    Absolute input is preserved as-is by the caller by not using this helper.
    """

    base_cwd = (cwd or Path.cwd()).expanduser().resolve()
    target = path.expanduser()
    if not target.is_absolute():
        target = (base_cwd / target).resolve()
    else:
        target = target.resolve()
    return os.path.relpath(target, start=resolve_compose_root(compose_file))
