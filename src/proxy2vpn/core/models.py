from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class VPNContainer(BaseModel):
    """Container configuration for VPN service."""

    name: str
    proxy_port: int
    control_port: int

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not _NAME_RE.match(value):
            raise ValueError("Use alphanumeric characters, '-' or '_' only")
        return value

    @field_validator("proxy_port", "control_port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 0 <= value <= 65535:
            raise ValueError("Port must be between 0 and 65535")
        return value


class VPNConfig(BaseModel):
    """VPN-specific configuration."""

    provider: str
    location: str
    profile: str
    environment: dict[str, str]
    labels: dict[str, str]


class VPNService(BaseModel):
    """Complete VPN service combining container and configuration."""

    container: VPNContainer
    config: VPNConfig

    model_config = ConfigDict(validate_assignment=True)

    @property
    def name(self) -> str:
        return self.container.name

    @property
    def port(self) -> int:
        return self.container.proxy_port

    @property
    def control_port(self) -> int:
        return self.container.control_port

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def profile(self) -> str:
        return self.config.profile

    @property
    def location(self) -> str:
        return self.config.location

    @property
    def environment(self) -> dict[str, str]:
        return self.config.environment

    @property
    def labels(self) -> dict[str, str]:
        return self.config.labels

    @classmethod
    def create(
        cls,
        name: str,
        port: int,
        control_port: int,
        provider: str,
        profile: str,
        location: str,
        environment: dict[str, str],
        labels: dict[str, str],
    ) -> "VPNService":
        """Backward compatible constructor for tests."""

        container = VPNContainer(name=name, proxy_port=port, control_port=control_port)
        config = VPNConfig(
            provider=provider,
            location=location,
            profile=profile,
            environment=environment,
            labels=labels,
        )
        return cls(container=container, config=config)

    @classmethod
    def from_compose_service(cls, name: str, service_def: dict) -> "VPNService":
        host_port = 0
        control_host_port = 0

        def _parse_port_mapping(p: object) -> tuple[int | None, int | None]:
            """Return (container_port, host_port) if parseable, else (None, None)."""

            try:
                if isinstance(p, dict):
                    target = p.get("target")
                    published = p.get("published") or p.get("host_port")
                    if target is not None and published is not None:
                        return int(target), int(published)
                    return None, None
                s = str(p)
                parts = s.split(":")
                cont_raw = parts[-1]
                cont_port = int(cont_raw.split("/")[0])
                if len(parts) == 2:
                    host = int(parts[0])
                else:
                    host = int(parts[-2])
                return cont_port, host
            except Exception:
                return None, None

        for p in service_def.get("ports", []) or []:
            cont, host = _parse_port_mapping(p)
            if cont == 8888 and host is not None:
                host_port = host
            elif cont == 8000 and host is not None:
                control_host_port = host

        env_dict: dict[str, str] = {}
        env_entries = service_def.get("environment", []) or []
        if isinstance(env_entries, dict):
            env_dict = {str(k): str(v) for k, v in env_entries.items()}
        else:
            for item in env_entries:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    env_dict[k] = v

        labels = dict(service_def.get("labels", {}))

        container = VPNContainer(
            name=name, proxy_port=host_port, control_port=control_host_port
        )
        config = VPNConfig(
            provider=labels.get(
                "vpn.provider", env_dict.get("VPN_SERVICE_PROVIDER", "")
            ),
            profile=labels.get("vpn.profile", ""),
            location=labels.get("vpn.location", env_dict.get("SERVER_CITIES", "")),
            environment=env_dict,
            labels=labels,
        )
        return cls(container=container, config=config)

    def to_compose_service(self) -> dict:
        env_list = [f"{k}={v}" for k, v in self.config.environment.items()]
        ports = [
            f"0.0.0.0:{self.container.proxy_port}:8888/tcp",
            f"127.0.0.1:{self.container.control_port}:8000/tcp",
        ]
        labels = dict(self.config.labels)
        labels.setdefault("vpn.port", str(self.container.proxy_port))
        labels.setdefault("vpn.control_port", str(self.container.control_port))
        return {
            "ports": ports,
            "environment": env_list,
            "labels": labels,
        }


class Profile(BaseModel):
    """Representation of a VPN profile stored as a YAML anchor."""

    name: str
    env_file: str
    image: str = "qmcgaw/gluetun"
    cap_add: list[str] = Field(default_factory=lambda: ["NET_ADMIN"])
    devices: list[str] = Field(default_factory=lambda: ["/dev/net/tun:/dev/net/tun"])

    _provider: str | None = PrivateAttr(default=None)

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not _NAME_RE.match(value):
            raise ValueError("Use alphanumeric characters, '-' or '_' only")
        return value

    @field_validator("env_file")
    @classmethod
    def _validate_env_file(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())

    @property
    def provider(self) -> str:
        """Get VPN provider from the environment file.

        Raises ValueError if VPN_PROVIDER is not specified in the profile's env file.
        """

        if self._provider is None:
            self._load_provider_from_env()

        if not self._provider:
            raise ValueError(
                f"Profile '{self.name}' is missing VPN_PROVIDER in {self.env_file}. "
                "Add 'VPN_PROVIDER=expressvpn' (or nordvpn, protonvpn, etc.) to the env file."
            )
        return self._provider

    def validate_env_file(self) -> list[str]:
        """Validate all required fields in the profile's environment file.

        Returns list of missing/invalid fields. Empty list means valid.
        """

        from proxy2vpn.adapters.docker_ops import _load_env_file

        env_vars = _load_env_file(self.env_file)
        errors: list[str] = []

        if not env_vars.get("VPN_PROVIDER"):
            errors.append(
                "VPN_PROVIDER is required (e.g., 'expressvpn', 'nordvpn', 'protonvpn')"
            )

        if not env_vars.get("OPENVPN_USER"):
            errors.append("OPENVPN_USER is required (your VPN account username)")

        if not env_vars.get("OPENVPN_PASSWORD"):
            errors.append("OPENVPN_PASSWORD is required (your VPN account password)")

        if env_vars.get("HTTPPROXY", "").lower() in ("on", "true", "1"):
            if not env_vars.get("HTTPPROXY_USER"):
                errors.append("HTTPPROXY_USER is required when HTTPPROXY=on")
            if not env_vars.get("HTTPPROXY_PASSWORD"):
                errors.append("HTTPPROXY_PASSWORD is required when HTTPPROXY=on")

        return errors

    def _load_provider_from_env(self) -> None:
        """Load provider information from the environment file."""

        from proxy2vpn.adapters.docker_ops import _load_env_file

        env_vars = _load_env_file(self.env_file)
        self._provider = env_vars.get("VPN_PROVIDER")

    @classmethod
    def from_anchor(cls, name: str, data: dict) -> "Profile":
        """Create a :class:`Profile` from an anchor section."""

        env_files = data.get("env_file", [])
        env_file = env_files[0] if env_files else ""
        return cls(
            name=name,
            env_file=env_file,
            image=data.get("image", "qmcgaw/gluetun"),
            cap_add=list(data.get("cap_add", [])),
            devices=list(data.get("devices", [])),
        )

    def to_anchor(self) -> dict:
        """Return a dictionary representing the profile configuration."""

        return {
            "image": self.image,
            "cap_add": list(self.cap_add),
            "devices": list(self.devices),
            "env_file": [self.env_file] if self.env_file else [],
        }
