from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .models import Profile, VPNService


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
        profile_key = f"x-vpn-base-{service.profile}"
        profile_map = self.data.get(profile_key)
        if profile_map is None:
            raise KeyError(f"Profile '{service.profile}' not found")
        svc_map = CommentedMap(service.to_compose_service())
        svc_map.merge_attrib = [profile_map]
        services[service.name] = svc_map
        self.save()

    def remove_service(self, name: str) -> None:
        services = self.data.get("services", {})
        if name not in services:
            raise KeyError(f"Service '{name}' not found")
        del services[name]
        self.save()

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def list_profiles(self) -> List[Profile]:
        profiles: List[Profile] = []
        for key, value in self.data.items():
            if key.startswith("x-vpn-base-"):
                name = key[len("x-vpn-base-") :]
                profiles.append(Profile.from_anchor(name, value))
        return profiles

    def get_profile(self, name: str) -> Profile:
        key = f"x-vpn-base-{name}"
        if key not in self.data:
            raise KeyError(f"Profile '{name}' not found")
        return Profile.from_anchor(name, self.data[key])

    def add_profile(self, profile: Profile) -> None:
        key = f"x-vpn-base-{profile.name}"
        if key in self.data:
            raise ValueError(f"Profile '{profile.name}' already exists")
        anchor_map = CommentedMap(profile.to_anchor())
        anchor_map.yaml_set_anchor(f"vpn-base-{profile.name}", always_dump=True)
        self.data[key] = anchor_map
        self.save()

    def remove_profile(self, name: str) -> None:
        key = f"x-vpn-base-{name}"
        if key not in self.data:
            raise KeyError(f"Profile '{name}' not found")
        del self.data[key]
        self.save()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def next_available_port(self, start: int = 0) -> int:
        """Find the next available host port starting from START.

        If START is 0 the search begins from 20000 which is the default
        range used by proxy2vpn.  Existing service ports are inspected and
        the first free port is returned.
        """

        port = start or 20000
        used = {svc.port for svc in self.list_services()}
        while port in used:
            port += 1
        return port

    def save(self) -> None:
        with self.compose_path.open("w", encoding="utf-8") as f:
            self.yaml.dump(self.data, f)
