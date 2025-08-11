"""Diagnostic analysis for proxy2vpn containers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List


@dataclass
class DiagnosticResult:
    """Result of analyzing a log line."""

    pattern: str
    severity: str
    message: str
    recommendation: str


class DiagnosticAnalyzer:
    """Analyze container logs for common error patterns."""

    def __init__(self) -> None:
        self.patterns: list[tuple[re.Pattern[str], str, str, str]] = [
            (
                re.compile(r"authentication (failed|failure)", re.I),
                "critical",
                "Authentication failure",
                "Check credentials and provider configuration.",
            ),
            (
                re.compile(r"certificate|ssl", re.I),
                "critical",
                "Certificate or SSL issue",
                "Verify certificates and TLS settings.",
            ),
            (
                re.compile(
                    r"(connection (refused|timed out|unreachable))|(network is unreachable)",
                    re.I,
                ),
                "critical",
                "Network connectivity issue",
                "Ensure network access and proxy settings.",
            ),
            (
                re.compile(r"rate limit", re.I),
                "warning",
                "Rate limiting detected",
                "Reduce request rate or check provider limits.",
            ),
            (
                re.compile(r"(dns (resolution|lookup) failed)|no such host", re.I),
                "critical",
                "DNS resolution failure",
                "Check DNS configuration or server availability.",
            ),
            (
                re.compile(r"openvpn", re.I),
                "info",
                "OpenVPN error",
                "Review OpenVPN configuration for issues.",
            ),
            (
                re.compile(r"wireguard", re.I),
                "info",
                "WireGuard error",
                "Review WireGuard configuration for issues.",
            ),
        ]

    def analyze(self, log_lines: Iterable[str]) -> List[DiagnosticResult]:
        """Return diagnostic results for matching log lines."""

        results: List[DiagnosticResult] = []
        for line in log_lines:
            for pattern, severity, message, recommendation in self.patterns:
                if pattern.search(line):
                    results.append(
                        DiagnosticResult(
                            pattern=pattern.pattern,
                            severity=severity,
                            message=message,
                            recommendation=recommendation,
                        )
                    )
        return results

    def health_score(self, results: Iterable[DiagnosticResult]) -> int:
        """Return a simple health score based on diagnostic results."""

        score = 100
        for res in results:
            if res.severity == "critical":
                score -= 40
            elif res.severity == "warning":
                score -= 20
            else:
                score -= 10
        return max(score, 0)
