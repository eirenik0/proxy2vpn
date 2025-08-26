"""Utilities for fetching and caching VPN server lists."""

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from proxy2vpn.common import abort
from proxy2vpn.core import config
from .http_client import (
    HTTPClient,
    HTTPClientConfig,
    HTTPClientError,
    RetryPolicy,
)


class ServerManager:
    """Manage gluetun server list information.

    The server list is downloaded from GitHub and cached locally to avoid
    repeated network requests. The cache is considered valid for ``ttl``
    seconds (24h by default).
    """

    def __init__(self, cache_dir: Path | None = None, ttl: int = 24 * 3600) -> None:
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.cache_file = self.cache_dir / "servers.json"
        self.ttl = ttl
        self.data: dict[str, dict] | None = None

    # ------------------------------------------------------------------
    # Fetching and caching
    # ------------------------------------------------------------------

    def _is_cache_valid(self) -> bool:
        if not self.cache_file.exists():
            return False
        age = time.time() - self.cache_file.stat().st_mtime
        return age < self.ttl

    # Public helper so callers can check freshness without reaching into privates
    def is_cache_fresh(self) -> bool:
        """Return True if the cached server list is newer than the TTL."""
        return self._is_cache_valid()

    def cache_age_seconds(self) -> float | None:
        """Return age of the cache in seconds, or None if cache missing."""
        if not self.cache_file.exists():
            return None
        return time.time() - self.cache_file.stat().st_mtime

    async def _download_servers(self, verify: bool) -> dict[str, dict]:
        parsed = urlparse(config.SERVER_LIST_URL)
        cfg = HTTPClientConfig(
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            timeout=config.DEFAULT_TIMEOUT,
            verify_ssl=verify,
            retry=RetryPolicy(attempts=config.MAX_RETRIES),
        )
        async with HTTPClient(cfg) as client:
            return await client.get(parsed.path)

    async def _fetch_and_cache(self, verify: bool) -> None:
        data = await self._download_servers(verify)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(data), encoding="utf-8")

    def update_servers(self, verify: bool = True) -> dict[str, dict]:
        """Synchronous wrapper around :meth:`update_servers`.

        Uses ``asyncio.run`` internally when a loop is not running.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # In an active loop, delegate to async variant via a helper task.
            # Callers should prefer the async method in this context.
            # For safety, fall back to reading cache if present; otherwise raise.
            if self.cache_file.exists() and self._is_cache_valid():
                with self.cache_file.open("r", encoding="utf-8") as f:
                    self.data = json.load(f)
                return self.data
            raise RuntimeError(
                "update_servers() called from within an active event loop"
            )

        return asyncio.run(self.fetch_server_list_async(verify))

    async def fetch_server_list_async(self, verify: bool = True) -> dict[str, dict]:
        """Fetch and cache the VPN server list asynchronously."""

        if not self._is_cache_valid():
            try:
                await self._fetch_and_cache(verify)
            except HTTPClientError as exc:
                abort("Failed to download server list", str(exc))
        with self.cache_file.open("r", encoding="utf-8") as f:
            self.data = json.load(f)
        return self.data

    # ------------------------------------------------------------------
    # Lazy load helper
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Ensure ``self.data`` is populated, fetching or reading cache if needed.

        This method performs a synchronous load using ``update_servers`` which
        internally handles async download via ``asyncio.run`` when the cache is
        stale or missing. This keeps call sites simple and non-async.
        """
        if self.data is not None:
            return
        # If cache exists and is valid, update_servers() will read it;
        # otherwise it will download and then read it.
        self.update_servers()

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    def list_providers(self) -> list[str]:
        self._ensure_loaded()
        data = self.data or {}
        return sorted(k for k in data.keys() if k != "version")

    def list_countries(self, provider: str) -> list[str]:
        """Return available countries for PROVIDER."""
        self._ensure_loaded()
        data = self.data or {}
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        countries = {srv.get("country") for srv in servers if srv.get("country")}
        return sorted(countries)

    def list_cities(self, provider: str, country: str) -> list[str]:
        """Return available cities for PROVIDER in COUNTRY."""
        self._ensure_loaded()
        data = self.data or {}
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        cities = {
            srv.get("city")
            for srv in servers
            if srv.get("country") == country and srv.get("city")
        }
        return sorted(cities)

    def parse_location(
        self, provider: str, location: str
    ) -> tuple[str | None, str | None]:
        """Split LOCATION into city and/or country components."""
        self._ensure_loaded()
        data = self.data or {}
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        loc = location.strip()
        if "," in loc:
            city, country = [part.strip() for part in loc.split(",", 1)]
            return city, country
        loc_l = loc.lower()
        for srv in servers:
            if srv.get("city", "").lower() == loc_l:
                return loc, None
            if srv.get("country", "").lower() == loc_l:
                return None, loc
        return loc, None

    def validate_location(self, provider: str, location: str) -> bool:
        """Return ``True`` if LOCATION exists for PROVIDER."""
        self._ensure_loaded()
        data = self.data or {}
        prov = data.get(provider, {})
        servers = prov.get("servers", [])
        if "," in location:
            city, country = [part.strip().lower() for part in location.split(",", 1)]
            for srv in servers:
                if (
                    srv.get("city", "").lower() == city
                    and srv.get("country", "").lower() == country
                ):
                    return True
            return False
        loc = location.lower()
        for srv in servers:
            if (
                srv.get("city", "").lower() == loc
                or srv.get("country", "").lower() == loc
            ):
                return True
        return False
