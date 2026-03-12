from pydantic import ValidationError
import pytest
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.core.models import VPNContainer, VPNService, ServiceCredentials


def test_vpncontainer_validates_fields():
    container = VPNContainer(name="svc", proxy_port=0, control_port=65535)
    assert container.name == "svc"

    with pytest.raises(ValidationError):
        VPNContainer(name="bad name", proxy_port=70000, control_port=-1)


def test_to_compose_service_omits_legacy_proxy_labels():
    svc = VPNService.create(
        name="svc",
        port=20000,
        control_port=30000,
        provider="test",
        profile="test",
        location="",
        environment={"VPN_SERVICE_PROVIDER": "test"},
        labels={"vpn.type": "vpn", "vpn.port": "20000", "vpn.profile": "test"},
        credentials=ServiceCredentials(
            httpproxy_user="user", httpproxy_password="pass"
        ),
    )
    service_def = svc.to_compose_service()
    assert "vpn.httpproxy_user" not in service_def["labels"]
    assert "vpn.httpproxy_password" not in service_def["labels"]


def test_from_compose_service_supports_legacy_proxy_labels():
    service = VPNService.from_compose_service(
        "svc",
        {
            "ports": ["20000:1194/tcp"],
            "environment": [],
            "labels": {
                "vpn.type": "vpn",
                "vpn.port": "20000",
                "vpn.profile": "test",
                "vpn.httpproxy_user": "legacy-user",
                "vpn.httpproxy_password": "legacy-pass",
            },
        },
    )
    assert service.credentials is not None
    assert service.credentials.httpproxy_user == "legacy-user"
    assert service.credentials.httpproxy_password == "legacy-pass"
