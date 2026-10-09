"""External endpoint configuration contains references, never credential values."""

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from proxy2vpn.core.egress import EndpointIdentity


class CredentialReference(BaseModel):
    username_env: str
    password_env: str
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("username_env", "password_env")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("Credentials require environment variable names")
        return value


class ProxyConnection(BaseModel):
    protocol: Literal["http_connect"] = "http_connect"
    host: str
    port: int = Field(ge=1, le=65535)
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
        if (
            len(value) > 253
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                for label in value.split(".")
            )
        ):
            raise ValueError(
                "Host must be an IP address or DNS hostname without a URL or credentials"
            )
        return value

    @property
    def proxy_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


# @lat: [[lat.md/egress#External Endpoint Configuration]]
class ExternalProxyEndpoint(BaseModel):
    id: str
    connection: ProxyConnection
    credentials: CredentialReference | None = None
    expected_egress_ips: list[str] = Field(default_factory=list)
    probe_urls: list[str] = Field(
        default_factory=lambda: [
            "https://ipinfo.io/ip",
            "https://ifconfig.me/ip",
        ],
        min_length=1,
        max_length=4,
    )
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError(
                "Endpoint id must contain only letters, numbers, underscores or hyphens"
            )
        return value

    @field_validator("expected_egress_ips")
    @classmethod
    def validate_ips(cls, value: list[str]) -> list[str]:
        return [str(ipaddress.ip_address(ip)) for ip in value]

    @field_validator("probe_urls")
    @classmethod
    def validate_probes(cls, value: list[str]) -> list[str]:
        for url in value:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError(
                    "Probe URLs must use HTTPS without credentials or fragments"
                )
            # Validate the port now rather than during a request.
            _ = parsed.port
        return value

    @property
    def identity(self) -> EndpointIdentity:
        return EndpointIdentity(self.id, "external_proxy")

    @property
    def name(self) -> str:
        return self.id

    @property
    def provider(self) -> str:
        return "external_proxy"

    @property
    def profile(self) -> None:
        return None
