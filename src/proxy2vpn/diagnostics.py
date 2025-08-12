"""Diagnostic analysis for proxy2vpn containers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List

from . import ip_utils


@dataclass
class DiagnosticResult:
    """Result of running a diagnostic check."""

    check: str
    passed: bool
    message: str
    recommendation: str
    persistent: bool = False


class DiagnosticAnalyzer:
    """Run VPN specific health checks on container logs and connectivity."""

    def __init__(self) -> None:
        self.patterns: dict[str, re.Pattern[str]] = {
            "auth_failure": re.compile(
                r"AUTH_FAILED|authentication (failed|failure)", re.I
            ),
            "tls_error": re.compile(r"tls|certificate|ssl", re.I),
            "dns_error": re.compile(
                r"(dns (resolution|lookup) failed)|no such host", re.I
            ),
        }

    def analyze_logs(self, log_lines: Iterable[str]) -> List[DiagnosticResult]:
        counts: dict[str, int] = {k: 0 for k in self.patterns}
        for line in log_lines:
            for key, pattern in self.patterns.items():
                if pattern.search(line):
                    counts[key] += 1
        results: List[DiagnosticResult] = []
        for key, count in counts.items():
            if count:
                msg, rec = self._messages(key)
                results.append(
                    DiagnosticResult(key, False, msg, rec, persistent=count > 1)
                )
        if not results:
            results.append(DiagnosticResult("logs", True, "No critical log errors", ""))
        return results

    def _messages(self, key: str) -> tuple[str, str]:
        mapping = {
            "auth_failure": (
                "Authentication failure detected",
                "Verify credentials and provider configuration.",
            ),
            "tls_error": (
                "TLS or certificate issue detected",
                "Check certificates and TLS settings.",
            ),
            "dns_error": (
                "DNS resolution failure detected",
                "Verify DNS settings or server availability.",
            ),
        }
        return mapping.get(key, (key, ""))

    def check_connectivity(self, port: int) -> List[DiagnosticResult]:
        results: List[DiagnosticResult] = []
        proxies = {
            "http": f"http://localhost:{port}",
            "https": f"http://localhost:{port}",
        }
        direct = ip_utils.fetch_ip()
        proxied = ip_utils.fetch_ip(proxies=proxies)
        details: list[str] = []
        if direct:
            details.append(f"direct={direct}")
        if proxied:
            details.append(f"proxied={proxied}")
        detail_msg = f" ({', '.join(details)})" if details else ""

        if not proxied:
            results.append(
                DiagnosticResult(
                    "connectivity",
                    False,
                    f"Connectivity test failed{detail_msg}",
                    "Ensure VPN container network is reachable.",
                )
            )
        elif proxied != direct:
            results.append(
                DiagnosticResult(
                    "dns_leak",
                    True,
                    f"No DNS leak detected{detail_msg}",
                    "",
                )
            )
        else:
            results.append(
                DiagnosticResult(
                    "dns_leak",
                    False,
                    f"Possible DNS leak detected{detail_msg}",
                    "Check firewall and kill switch settings.",
                )
            )
        return results

    async def check_connectivity_async(self, port: int) -> List[DiagnosticResult]:
        import asyncio

        results: List[DiagnosticResult] = []
        proxies = {
            "http": f"http://localhost:{port}",
            "https": f"http://localhost:{port}",
        }

        # Fetch both IPs concurrently for faster diagnostics
        direct_task = asyncio.create_task(ip_utils.fetch_ip_async())
        proxied_task = asyncio.create_task(ip_utils.fetch_ip_async(proxies=proxies))

        direct, proxied = await asyncio.gather(direct_task, proxied_task)
        details: list[str] = []
        if direct:
            details.append(f"direct={direct}")
        if proxied:
            details.append(f"proxied={proxied}")
        detail_msg = f" ({', '.join(details)})" if details else ""

        if not proxied:
            results.append(
                DiagnosticResult(
                    "connectivity",
                    False,
                    f"Connectivity test failed{detail_msg}",
                    "Ensure VPN container network is reachable.",
                )
            )
        elif proxied != direct:
            results.append(
                DiagnosticResult(
                    "dns_leak",
                    True,
                    f"No DNS leak detected{detail_msg}",
                    "",
                )
            )
        else:
            results.append(
                DiagnosticResult(
                    "dns_leak",
                    False,
                    f"Possible DNS leak detected{detail_msg}",
                    "Check firewall and kill switch settings.",
                )
            )
        return results

    def analyze(
        self, log_lines: Iterable[str], port: int | None = None
    ) -> List[DiagnosticResult]:
        results = self.analyze_logs(log_lines)
        if port:
            results.extend(self.check_connectivity(port))
        return results

    async def analyze_async(
        self, log_lines: Iterable[str], port: int | None = None
    ) -> List[DiagnosticResult]:
        results = self.analyze_logs(log_lines)
        if port:
            results.extend(await self.check_connectivity_async(port))
        return results

    def health_score(self, results: Iterable[DiagnosticResult]) -> int:
        """Return an aggregate health score for diagnostic results.

        Starts at ``100`` and deducts points for each failing check. Persistent
        issues count more heavily than transient ones. The score is clamped to a
        0–100 range.
        """

        score = 100
        for res in results:
            if not res.passed:
                score -= 50 if res.persistent else 25
        return max(0, min(100, score))
