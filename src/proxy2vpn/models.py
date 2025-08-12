from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from .validators import sanitize_name, sanitize_path, validate_port


@dataclass
class VPNService:
    name: str
    port: int
    provider: str
    profile: str
    location: str
    environment: Dict[str, str]
    labels: Dict[str, str]

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        self.port = validate_port(self.port)

    @classmethod
    def from_compose_service(cls, name: str, service_def: Dict) -> "VPNService":
        ports = service_def.get("ports", [])
        host_port = 0
        if ports:
            mapping = str(ports[0])
            parts = mapping.split(":")
            if len(parts) >= 3:
                host_port = int(parts[1])
            elif len(parts) == 2:
                host_port = int(parts[0])
            else:
                host_port = int(mapping)
        env_list = service_def.get("environment", [])
        env_dict: Dict[str, str] = {}
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
            provider=provider,
            profile=profile,
            location=location,
            environment=env_dict,
            labels=labels,
        )

    def to_compose_service(self) -> Dict:
        env_list = [f"{k}={v}" for k, v in self.environment.items()]
        service = {
            "ports": [f"{self.port}:8888/tcp"],
            "environment": env_list,
            "labels": self.labels,
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
    cap_add: List[str] = field(default_factory=lambda: ["NET_ADMIN"])
    devices: List[str] = field(default_factory=lambda: ["/dev/net/tun:/dev/net/tun"])

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        # Store resolved path but keep as string for YAML serialization
        self.env_file = str(sanitize_path(Path(self.env_file)))

    @classmethod
    def from_anchor(cls, name: str, data: Dict) -> "Profile":
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

    def to_anchor(self) -> Dict:
        """Return a dictionary representing the profile configuration."""

        return {
            "image": self.image,
            "cap_add": list(self.cap_add),
            "devices": list(self.devices),
            "env_file": [self.env_file] if self.env_file else [],
        }
