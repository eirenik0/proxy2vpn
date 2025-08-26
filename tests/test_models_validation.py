from pydantic import ValidationError
import pytest

from proxy2vpn.core.models import VPNContainer


def test_vpncontainer_validates_fields():
    container = VPNContainer(name="svc", proxy_port=0, control_port=65535)
    assert container.name == "svc"

    with pytest.raises(ValidationError):
        VPNContainer(name="bad name", proxy_port=70000, control_port=-1)
