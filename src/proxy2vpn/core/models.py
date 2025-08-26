from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator
from proxy2vpn.adapters.compose_utils import parse_env, iter_port_mappings
from proxy2vpn.core import config


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


class ServiceCredentials(BaseModel):
    """Service-specific credential overrides.

    These credentials override the default profile credentials for HTTP proxy
    authentication, allowing unique passwords per service.
    """

    httpproxy_user: str | None = None
    httpproxy_password: str | None = None


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
    credentials: ServiceCredentials | None = None

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
        credentials: ServiceCredentials | None = None,
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
        return cls(container=container, config=config, credentials=credentials)

    @classmethod
    def from_compose_service(cls, name: str, service_def: dict) -> "VPNService":
        host_port = 0
        control_host_port = 0

        for host, cont in iter_port_mappings(service_def.get("ports", []) or []):
            if cont == 8888:
                host_port = host
            elif cont == 8000:
                control_host_port = host

        env_entries = service_def.get("environment", []) or []
        env_dict: dict[str, str] = parse_env(env_entries)

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
        # Parse service-specific credentials from labels
        credentials = None
        httpproxy_user = labels.get("vpn.httpproxy_user")
        httpproxy_password = labels.get("vpn.httpproxy_password")

        if httpproxy_user or httpproxy_password:
            credentials = ServiceCredentials(
                httpproxy_user=httpproxy_user,
                httpproxy_password=httpproxy_password,
            )

        return cls(container=container, config=config, credentials=credentials)

    def validate_httpproxy_config(self) -> list[str]:
        """Validate HTTP proxy configuration for this service.

        Returns list of validation errors. Empty list means valid.
        """
        errors = []

        # Check if HTTP proxy is enabled in environment or via credentials
        env_dict = dict(self.config.environment)

        # Apply credential overrides to get effective configuration
        if self.credentials:
            if self.credentials.httpproxy_user:
                env_dict["HTTPPROXY_USER"] = self.credentials.httpproxy_user
            if self.credentials.httpproxy_password:
                env_dict["HTTPPROXY_PASSWORD"] = self.credentials.httpproxy_password

        httpproxy_enabled = env_dict.get("HTTPPROXY", "").lower() in ("on", "true", "1")

        if httpproxy_enabled:
            if not env_dict.get("HTTPPROXY_USER"):
                errors.append("HTTPPROXY_USER is required when HTTPPROXY=on")
            if not env_dict.get("HTTPPROXY_PASSWORD"):
                errors.append("HTTPPROXY_PASSWORD is required when HTTPPROXY=on")

        return errors

    def to_compose_service(self) -> dict:
        # Start with base environment
        env_dict = dict(self.config.environment)

        # Override with service-specific credentials if provided
        if self.credentials:
            if self.credentials.httpproxy_user:
                env_dict["HTTPPROXY_USER"] = self.credentials.httpproxy_user
            if self.credentials.httpproxy_password:
                env_dict["HTTPPROXY_PASSWORD"] = self.credentials.httpproxy_password

        env_list = [f"{k}={v}" for k, v in env_dict.items()]
        ports = [
            f"0.0.0.0:{self.container.proxy_port}:8888/tcp",
            f"127.0.0.1:{self.container.control_port}:8000/tcp",
        ]
        labels = dict(self.config.labels)
        labels.setdefault("vpn.port", str(self.container.proxy_port))
        labels.setdefault("vpn.control_port", str(self.container.control_port))

        # Store service credentials in labels for serialization
        if self.credentials:
            if self.credentials.httpproxy_user:
                labels["vpn.httpproxy_user"] = self.credentials.httpproxy_user
            if self.credentials.httpproxy_password:
                labels["vpn.httpproxy_password"] = self.credentials.httpproxy_password
        volumes = [
            f"{config.CONTROL_AUTH_CONFIG_FILE}:/gluetun/auth/config.toml:ro",
        ]
        return {
            "ports": ports,
            "environment": env_list,
            "labels": labels,
            "volumes": volumes,
        }


class Profile(BaseModel):
    """Representation of a VPN profile stored as a YAML anchor."""

    name: str
    env_file: str
    image: str = "qmcgaw/gluetun"
    cap_add: list[str] = Field(default_factory=lambda: ["NET_ADMIN"])
    devices: list[str] = Field(default_factory=lambda: ["/dev/net/tun:/dev/net/tun"])

    _provider: str | None = PrivateAttr(default=None)
    _vpn_type: str | None = PrivateAttr(default=None)
    _base_dir: Path | None = PrivateAttr(default=None)

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
        # Keep the original user-provided path for YAML portability.
        # Resolution is handled at runtime relative to the compose file dir.
        return str(value)

    def _resolve_env_path(self) -> Path:
        """Resolve env_file path relative to the compose base dir if available."""
        p = Path(self.env_file)
        if not p.is_absolute() and self._base_dir is not None:
            return (self._base_dir / p).expanduser().resolve()
        return p.expanduser().resolve()

    @property
    def provider(self) -> str:
        """Get VPN provider from the environment file.

        Raises ValueError if VPN_SERVICE_PROVIDER is not specified in the profile's env file.
        """

        if self._provider is None:
            self._load_provider_from_env()

        if not self._provider:
            raise ValueError(
                f"Profile '{self.name}' is missing VPN_SERVICE_PROVIDER in {self.env_file}. "
                "Add 'VPN_SERVICE_PROVIDER=expressvpn' (or nordvpn, protonvpn, etc.) to the env file."
            )
        return self._provider

    @property
    def vpn_type(self) -> str:
        """Get VPN type from the environment file, defaulting to openvpn."""

        if self._vpn_type is None:
            self._load_vpn_type_from_env()
        return self._vpn_type or "openvpn"

    def validate_env_file(self) -> list[str]:
        """Validate all required fields in the profile's environment file.

        Returns list of missing/invalid fields. Empty list means valid.
        """

        from proxy2vpn.adapters.docker_ops import _load_env_file
        from proxy2vpn.adapters import server_manager

        env_vars = _load_env_file(str(self._resolve_env_path()))
        errors: list[str] = []

        vpn_type = env_vars.get("VPN_TYPE", "openvpn").strip().lower()
        if vpn_type not in ("openvpn", "wireguard"):
            errors.append("VPN_TYPE must be 'openvpn' or 'wireguard'")

        provider = env_vars.get("VPN_SERVICE_PROVIDER")
        if not provider:
            errors.append(
                "VPN_SERVICE_PROVIDER is required (e.g., 'expressvpn', 'nordvpn', 'protonvpn')"
            )
        else:
            supported = server_manager.ServerManager().list_providers()
            if provider.strip().lower() not in supported:
                errors.append(
                    f"Unsupported VPN_SERVICE_PROVIDER '{provider}'. "
                    "Run 'proxy2vpn servers list-providers' to see supported providers"
                )

        if vpn_type == "openvpn":
            if not env_vars.get("OPENVPN_USER"):
                errors.append("OPENVPN_USER is required (your VPN account username)")

            if not env_vars.get("OPENVPN_PASSWORD"):
                errors.append(
                    "OPENVPN_PASSWORD is required (your VPN account password)"
                )

        if env_vars.get("HTTPPROXY", "").lower() in ("on", "true", "1"):
            if not env_vars.get("HTTPPROXY_USER"):
                errors.append("HTTPPROXY_USER is required when HTTPPROXY=on")
            if not env_vars.get("HTTPPROXY_PASSWORD"):
                errors.append("HTTPPROXY_PASSWORD is required when HTTPPROXY=on")

        return errors

    def _load_provider_from_env(self) -> None:
        """Load provider information from the environment file."""

        from proxy2vpn.adapters.docker_ops import _load_env_file

        env_vars = _load_env_file(str(self._resolve_env_path()))
        self._provider = env_vars.get("VPN_SERVICE_PROVIDER")

    def _load_vpn_type_from_env(self) -> None:
        """Load VPN type information from the environment file."""

        from proxy2vpn.adapters.docker_ops import _load_env_file

        env_vars = _load_env_file(str(self._resolve_env_path()))
        self._vpn_type = env_vars.get("VPN_TYPE", "openvpn")

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
