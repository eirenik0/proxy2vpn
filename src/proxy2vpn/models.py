from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .validators import sanitize_name, sanitize_path, validate_port


@dataclass
class VPNService:
    name: str
    port: int
    provider: str
    profile: str
    location: str
    environment: dict[str, str]
    labels: dict[str, str]
    control_port: int = 0

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        self.port = validate_port(self.port)
        self.control_port = validate_port(self.control_port)

    @classmethod
    def from_compose_service(cls, name: str, service_def: dict) -> "VPNService":
        ports = service_def.get("ports", [])
        host_port = 0
        control_port = 0
        for mapping in ports:
            mapping = str(mapping)
            parts = mapping.split(":")
            if len(parts) >= 3:
                host = int(parts[1])
                container = parts[2]
            elif len(parts) == 2:
                host = int(parts[0])
                container = parts[1]
            else:
                host = int(mapping)
                container = ""
            container_port = container.split("/")[0]
            if container_port == "8888" and host_port == 0:
                host_port = host
            elif container_port == "8000" and control_port == 0:
                control_port = host
        env_list = service_def.get("environment", [])
        env_dict: dict[str, str] = {}
        for item in env_list:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                env_dict[k] = v
        labels = dict(service_def.get("labels", {}))
        provider = labels.get("vpn.provider", env_dict.get("VPN_SERVICE_PROVIDER", ""))
        profile = labels.get("vpn.profile", "")
        location = labels.get("vpn.location", env_dict.get("SERVER_CITIES", ""))
        return cls(
            name=name,
            port=host_port,
            control_port=control_port,
            provider=provider,
            profile=profile,
            location=location,
            environment=env_dict,
            labels=labels,
        )

    def to_compose_service(self) -> dict:
        env_list = [f"{k}={v}" for k, v in self.environment.items()]
        ports = [f"{self.port}:8888/tcp"]
        if self.control_port:
            ports.append(f"{self.control_port}:8000/tcp")
        labels = dict(self.labels)
        labels.setdefault("vpn.port", str(self.port))
        if self.control_port:
            labels.setdefault("vpn.control_port", str(self.control_port))
        service = {
            "ports": ports,
            "environment": env_list,
            "labels": labels,
        }
        return service


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
