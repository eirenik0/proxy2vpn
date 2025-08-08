from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ruamel.yaml import YAML

from .models import VPNService


class ComposeManager:
    """Manage docker-compose files for VPN services."""

    def __init__(self, compose_path: Path) -> None:
        self.compose_path = compose_path
        self.yaml = YAML()
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        with self.compose_path.open("r", encoding="utf-8") as f:
            return self.yaml.load(f)

    @property
    def config(self) -> Dict[str, Any]:
        """Return global configuration stored under x-config."""
        return self.data.get("x-config", {})

    def list_services(self) -> List[VPNService]:
        services = self.data.get("services", {})
        return [VPNService.from_compose_service(name, svc) for name, svc in services.items()]

    def get_service(self, name: str) -> VPNService:
        services = self.data.get("services", {})
        if name not in services:
            raise KeyError(f"Service '{name}' not found")
        return VPNService.from_compose_service(name, services[name])

    def add_service(self, service: VPNService) -> None:
        services = self.data.setdefault("services", {})
        if service.name in services:
            raise ValueError(f"Service '{service.name}' already exists")
        services[service.name] = service.to_compose_service()
        self.save()

    def remove_service(self, name: str) -> None:
        services = self.data.get("services", {})
        if name not in services:
            raise KeyError(f"Service '{name}' not found")
        del services[name]
        self.save()

    def save(self) -> None:
        with self.compose_path.open("w", encoding="utf-8") as f:
            self.yaml.dump(self.data, f)
