"""Utilities for retrieving the public IP address."""

from __future__ import annotations

from typing import Mapping

import requests

IP_SERVICES = ("https://ipinfo.io/ip", "https://ifconfig.me")


def fetch_ip(proxies: Mapping[str, str] | None = None, timeout: int = 5) -> str:
    """Return the public IP address using external services.

    Tries multiple providers and returns the first successful response.
    """
    for url in IP_SERVICES:
        try:
            resp = requests.get(url, proxies=proxies, timeout=timeout)
            ip = resp.text.strip()
            if ip:
                return ip
        except requests.RequestException:
            continue
    return ""
