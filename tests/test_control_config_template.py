import pathlib
import sys

# Ensure src is importable when running tests directly
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.core import config


def test_control_api_endpoints_are_v1_and_correct():
    # Expected mapping pinned to current supported routes
    expected = {
        "status": "/v1/openvpn/status",
        "openvpn": "/v1/openvpn/status",  # legacy key maps to status
        "ip": "/v1/publicip/ip",
        "openvpn_status": "/v1/openvpn/status",
        "dns_status": "/v1/dns/status",
        "updater_status": "/v1/updater/status",
        "port_forward": "/v1/openvpn/portforwarded",
    }
    assert config.CONTROL_API_ENDPOINTS == expected


def test_control_auth_config_template_contains_supported_routes_only():
    tpl = config.CONTROL_AUTH_CONFIG_TEMPLATE

    # Required present routes
    must_include = [
        # OpenVPN
        "GET /v1/openvpn/status",
        "PUT /v1/openvpn/status",
        "GET /v1/openvpn/portforwarded",
        "GET /v1/openvpn/settings",
        # DNS
        "GET /v1/dns/status",
        "PUT /v1/dns/status",
        # Updater
        "GET /v1/updater/status",
        "PUT /v1/updater/status",
        # Public IP
        "GET /v1/publicip/ip",
    ]
    for s in must_include:
        assert s in tpl, f"Missing route in template: {s}"

    # Deprecated/unsupported routes must be absent
    must_exclude = [
        "GET /v1/status",  # old/nonexistent
        "GET /v1/ip",  # moved to /v1/publicip/ip
        "POST /v1/openvpn",  # not supported
        # Also ensure no non-v1 root-level old paths
        '"GET /status"',
        '"GET /ip"',
        '"POST /openvpn"',
        "/openvpn/restart",  # not part of template
    ]
    for s in must_exclude:
        assert s not in tpl, f"Unexpected deprecated route in template: {s}"
