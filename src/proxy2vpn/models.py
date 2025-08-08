from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class VPNService:
    name: str
    port: int
    provider: str
    profile: str
    location: str
    environment: Dict[str, str]
    labels: Dict[str, str]

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
