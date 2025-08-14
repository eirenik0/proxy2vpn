from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import shutil

from filelock import FileLock
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, merge_attrib

from .models import Profile, VPNService
from .compose_validator import validate_compose

# Minimal compose template used when initializing a new project
INITIAL_COMPOSE_TEMPLATE = """\
# proxy2vpn compose file
# Define VPN profiles with x-vpn-base-<name> entries
# and add services under the 'services' section.
services: {}
networks:
  proxy2vpn_network:
    driver: bridge
    name: proxy2vpn_network
"""


class ComposeManager:
    """Manage docker-compose files for VPN services."""

    def __init__(self, compose_path: Path) -> None:
        self.compose_path = compose_path
        self.yaml = YAML()
        self.lock = FileLock(str(compose_path) + ".lock")
        self.data: CommentedMap = self._load()

    def _load(self) -> CommentedMap:
        if not self.compose_path.exists():
            raise FileNotFoundError(
                f"compose file '{self.compose_path}' not found. Run 'proxy2vpn system init' to create it."
            )
        backup_path = self.compose_path.with_suffix(self.compose_path.suffix + ".bak")
        with self.lock:
            try:
                with self.compose_path.open("r", encoding="utf-8") as f:
                    data = self.yaml.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("compose file does not contain a mapping")
                    # Ensure we have a CommentedMap for proper YAML manipulation
                    if not isinstance(data, CommentedMap):
                        data = CommentedMap(data)
                # basic validation: ensure YAML structure can be parsed
                validate_compose(self.compose_path)
                return data
            except Exception:
                if backup_path.exists():
                    with backup_path.open("r", encoding="utf-8") as f:
                        data = self.yaml.load(f)
                    shutil.copy2(backup_path, self.compose_path)
                    return data
                raise

    @staticmethod
    def create_initial_compose(path: Path, force: bool = False) -> None:
        """Create a minimal compose file at PATH.

        If the file already exists and ``force`` is False a FileExistsError is
        raised.  The generated file contains an empty services section and the
        network configuration required by proxy2vpn.
        """

        if path.exists() and not force:
            raise FileExistsError(f"compose file '{path}' already exists")

        yaml = YAML()
        data = yaml.load(INITIAL_COMPOSE_TEMPLATE)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)

    @property
    def config(self) -> dict[str, Any]:
        """Return global configuration stored under x-config."""
        return self.data.get("x-config", {})

    def list_services(self) -> list[VPNService]:
        services = self.data.get("services", {})
        return [
            VPNService.from_compose_service(name, svc) for name, svc in services.items()
        ]

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
        setattr(svc_map, merge_attrib, [(0, profile_map)])
        services[service.name] = svc_map
        self.save()

    def remove_service(self, name: str) -> None:
        services = self.data.get("services", {})
        if name not in services:
            raise KeyError(f"Service '{name}' not found")
        del services[name]
        self.save()

    def update_service(self, service: VPNService) -> None:
        """Update an existing service with new configuration"""
        services = self.data.get("services", {})
        if service.name not in services:
            raise KeyError(f"Service '{service.name}' not found")

        # Get the profile for merging
        profile_key = f"x-vpn-base-{service.profile}"
        profile_map = self.data.get(profile_key)
        if profile_map is None:
            raise KeyError(f"Profile '{service.profile}' not found")

        # Update service configuration
        svc_map = CommentedMap(service.to_compose_service())
        setattr(svc_map, merge_attrib, [(0, profile_map)])  # YAML merge
        services[service.name] = svc_map
        self.save()

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[Profile]:
        profiles: list[Profile] = []
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
        if "services" in self.data:
            idx = list(self.data.keys()).index("services")
            self.data.insert(idx, key, anchor_map)
        else:
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

    def next_available_control_port(self, start: int = 0) -> int:
        """Find the next available control port starting from START.

        Control ports are allocated separately from proxy ports to prevent
        conflicts. If START is 0, begins from 18000 (default control port range).
        """
        from .config import DEFAULT_CONTROL_PORT_START

        port = start or DEFAULT_CONTROL_PORT_START
        services = self.list_services()
        used_control = {getattr(svc, "control_port", 0) for svc in services}
        used_proxy = {svc.port for svc in services}
        used_ports = used_control | used_proxy

        while port in used_ports:
            port += 1
        return port

    def get_all_used_ports(self) -> set[int]:
        """Get all ports (proxy + control) currently in use by services."""
        services = self.list_services()
        proxy_ports = {svc.port for svc in services}
        control_ports = {getattr(svc, "control_port", 0) for svc in services}
        return proxy_ports | control_ports - {0}  # Remove 0 (unset ports)

    def save(self) -> None:
        backup_path = self.compose_path.with_suffix(self.compose_path.suffix + ".bak")
        tmp_path = self.compose_path.with_suffix(self.compose_path.suffix + ".tmp")
        with self.lock:
            try:
                if self.compose_path.exists():
                    shutil.copy2(self.compose_path, backup_path)
                with tmp_path.open("w", encoding="utf-8") as f:
                    self.yaml.dump(self.data, f)
                os.replace(tmp_path, self.compose_path)
            except Exception:
                if backup_path.exists():
                    shutil.copy2(backup_path, self.compose_path)
                raise
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except FileNotFoundError:
                        pass
