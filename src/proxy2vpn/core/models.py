from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from proxy2vpn.adapters.validators import sanitize_name, sanitize_path, validate_port


@dataclass
class VPNContainer:
    """Container configuration for VPN service."""

    name: str
    proxy_port: int
    control_port: int

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        self.proxy_port = validate_port(self.proxy_port)
        self.control_port = validate_port(self.control_port)


@dataclass
class VPNConfig:
    """VPN-specific configuration."""

    provider: str
    location: str
    profile: str
    environment: dict[str, str]
    labels: dict[str, str]


@dataclass
class VPNService:
    """Complete VPN service combining container and configuration."""

    container: VPNContainer
    config: VPNConfig

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
                    # Compose long syntax: {target: 8888, published: 12345, protocol: tcp}
                    target = p.get("target")
                    published = p.get("published") or p.get("host_port")
                    if target is not None and published is not None:
                        return int(target), int(published)
                    return None, None
                # Treat everything else as string-like
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
        # Parse environment variables (list or mapping)
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

        # Create container and config components
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


@dataclass
class Profile:
    """Representation of a VPN profile stored as a YAML anchor.

    The profile contains the base configuration used by VPN services.  In
    the compose file profiles are stored under a key of the form
    ``x-vpn-base-<name>`` with an anchor ``&vpn-base-<name>``.  Services can
    then merge the profile using ``<<: *vpn-base-<name>``.
    """

    name: str
    env_file: str
    image: str = "qmcgaw/gluetun"
    cap_add: list[str] = field(default_factory=lambda: ["NET_ADMIN"])
    devices: list[str] = field(default_factory=lambda: ["/dev/net/tun:/dev/net/tun"])

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        # Store resolved path but keep as string for YAML serialization
        self.env_file = str(sanitize_path(Path(self.env_file)))

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
