import pathlib
import sys

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import ip_utils


def test_parse_ip_from_html():
    html = "<html><body>IP is 203.0.113.5</body></html>"
    assert ip_utils._parse_ip(html) == "203.0.113.5"


def test_parse_ip_invalid_text():
    assert ip_utils._parse_ip("<html></html>") == ""
