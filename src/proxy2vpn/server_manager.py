"""Utilities for fetching and caching VPN server lists."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

import requests

from . import config


class ServerManager:
    """Manage gluetun server list information.

    The server list is downloaded from GitHub and cached locally to avoid
    repeated network requests.  The cache is considered valid for ``ttl``
    seconds (24h by default).
    """

    def __init__(self, cache_dir: Path | None = None, ttl: int = 24 * 3600) -> None:
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_file = self.cache_dir / "servers.json"
        self.ttl = ttl
        self.data: Dict[str, Dict] | None = None

    # ------------------------------------------------------------------
    # Fetching and caching
    # ------------------------------------------------------------------

    def _is_cache_valid(self) -> bool:
        if not self.cache_file.exists():
            return False
        age = time.time() - self.cache_file.stat().st_mtime
        return age < self.ttl

    def update_servers(self) -> Dict[str, Dict]:
        """Fetch the server list, using the cache when possible."""

        if not self._is_cache_valid():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            response = requests.get(config.SERVER_LIST_URL, timeout=30)
            response.raise_for_status()
            self.cache_file.write_text(response.text, encoding="utf-8")
        with self.cache_file.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        return self.data

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    def list_providers(self) -> List[str]:
        data = self.data or self.update_servers()
        return sorted(data.keys())

    def list_countries(self, provider: str) -> List[str]:
        data = self.data or self.update_servers()
        prov = data.get(provider, {})
        return sorted(prov.keys())
