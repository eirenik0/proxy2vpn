import pathlib
import sys

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters import proxy_utils


def test_extract_proxy_credentials_from_env():
    username, password = proxy_utils.extract_proxy_credentials_from_env(
        ["HTTPPROXY_USER=user", "HTTPPROXY_PASSWORD=secret", "FOO=bar"]
    )
    assert username == "user"
    assert password == "secret"


def test_proxy_url_and_redaction():
    urls = proxy_utils.build_proxy_urls(
        8080, username="user", password="pass"
    )
    assert urls["http"] == "http://user:pass@localhost:8080"
    assert proxy_utils.redact_proxy_url(urls["http"]) == "http://***:***@localhost:8080"
