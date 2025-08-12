"""Utilities for fetching and caching VPN server lists."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

import aiohttp
import requests
import typer

from . import config
from .utils import abort


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

    def update_servers(self, verify: bool = True) -> Dict[str, Dict]:
        """Fetch the server list, using the cache when possible.

        Parameters
        ----------
        verify:
            Whether to verify SSL certificates when downloading the server
            list. Set to ``False`` for troubleshooting.
        """

        if not self._is_cache_valid():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                response = requests.get(
                    config.SERVER_LIST_URL,
                    timeout=30,
                    verify=verify,
                    stream=True,
                )
            except requests.exceptions.SSLError:
                abort(
                    "Failed to download server list (SSL error)",
                    "Check network connection or CA certificates",
                )
            except requests.exceptions.RequestException as exc:
                abort("Failed to download server list", str(exc))
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0)) or None
            chunks: list[bytes] = []
            with typer.progressbar(length=total, label="Downloading server list") as pb:
                for chunk in response.iter_content(chunk_size=8192):
                    chunks.append(chunk)
                    pb.update(len(chunk))
            self.cache_file.write_bytes(b"".join(chunks))
        with self.cache_file.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        return self.data

    async def fetch_server_list_async(self, verify: bool = True) -> Dict:
        """Fetch and cache the VPN server list asynchronously.

        Returns
        -------
        dict
            The server list data as a dictionary.

        Parameters
        ----------
        verify:
            Whether to verify SSL certificates when downloading the server
            list. Set to ``False`` for troubleshooting.
        """

        if not self._is_cache_valid():
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(verify_ssl=verify)

            try:
                async with aiohttp.ClientSession(
                    timeout=timeout, connector=connector
                ) as session:
                    async with session.get(config.SERVER_LIST_URL) as response:
                        response.raise_for_status()
                        total = int(response.headers.get("content-length", 0)) or None
                        chunks: list[bytes] = []

                        with typer.progressbar(
                            length=total, label="Downloading server list"
                        ) as pb:
                            async for chunk in response.content.iter_chunked(8192):
                                chunks.append(chunk)
                                pb.update(len(chunk))

                        self.cache_file.write_bytes(b"".join(chunks))

            except aiohttp.ClientSSLError:
                abort(
                    "Failed to download server list (SSL error)",
                    "Check network connection or CA certificates",
                )
            except aiohttp.ClientError as exc:
                abort("Failed to download server list", str(exc))

        with self.cache_file.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        return self.data

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    def list_providers(self) -> List[str]:
        data = self.data or self.update_servers()
        return sorted(k for k in data.keys() if k != "version")

    def list_countries(self, provider: str) -> List[str]:
        """Return available countries for PROVIDER."""

        data = self.data or self.update_servers()
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        countries = {srv.get("country") for srv in servers if srv.get("country")}
        return sorted(countries)

    def list_cities(self, provider: str, country: str) -> List[str]:
        """Return available cities for PROVIDER in COUNTRY."""

        data = self.data or self.update_servers()
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        cities = {
            srv.get("city")
            for srv in servers
            if srv.get("country") == country and srv.get("city")
        }
        return sorted(cities)

    def validate_location(self, provider: str, location: str) -> bool:
        """Return ``True`` if LOCATION exists for PROVIDER."""

        data = self.data or self.update_servers()
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        loc = location.lower()
        for srv in servers:
            if (
                srv.get("city", "").lower() == loc
                or srv.get("country", "").lower() == loc
            ):
                return True
        return False
