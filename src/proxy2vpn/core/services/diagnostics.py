"""Diagnostic analysis for proxy2vpn containers."""

from __future__ import annotations

from typing import Iterable
from pydantic import BaseModel, ConfigDict

from proxy2vpn.adapters import ip_utils


class DiagnosticResult(BaseModel):
    """Result of running a diagnostic check."""

    check: str
    passed: bool
    message: str
    recommendation: str
    persistent: bool = False

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class DiagnosticAnalyzer:
    """Simple VPN health checks on container logs and connectivity."""

    def analyze_logs(self, log_lines: Iterable[str]) -> list[DiagnosticResult]:
        """Simple log analysis - detect common errors and persistence."""
        lines = [str(line) for line in log_lines]
        log_text = " ".join(lines).lower()

        # Authentication failures, detect repeated occurrences as persistent
        if ("auth" in log_text and "fail" in log_text) or any(
            "auth_failed" in line.lower() for line in lines
        ):
            persistent = sum("auth_failed" in line.lower() for line in lines) >= 2
            return [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Authentication failure detected",
                    recommendation="Verify credentials and provider configuration.",
                    persistent=persistent,
                )
            ]

        if "tls" in log_text or "certificate" in log_text or "ssl" in log_text:
            return [
                DiagnosticResult(
                    check="tls_error",
                    passed=False,
                    message="TLS or certificate issue detected",
                    recommendation="Check certificates and TLS settings.",
                )
            ]

        if "dns" in log_text and "fail" in log_text:
            return [
                DiagnosticResult(
                    check="dns_error",
                    passed=False,
                    message="DNS resolution failure detected",
                    recommendation="Verify DNS settings or server availability.",
                )
            ]

        return [
            DiagnosticResult(
                check="logs",
                passed=True,
                message="No critical log errors",
                recommendation="",
            )
        ]

    def check_connectivity(self, port: int) -> list[DiagnosticResult]:
        """Connectivity + DNS leak checks with informative messages."""
        proxies = {
            "http": f"http://localhost:{port}",
            "https": f"http://localhost:{port}",
        }

        try:
            direct = ip_utils.fetch_ip()
            proxied = ip_utils.fetch_ip(proxies=proxies)

            results: list[DiagnosticResult] = []
            if not proxied:
                msg = f"Connectivity test failed (direct={direct})"
                results.append(
                    DiagnosticResult(
                        check="connectivity",
                        passed=False,
                        message=msg,
                        recommendation="Ensure VPN container network is reachable.",
                    )
                )
                return results

            msg = f"direct={direct} proxied={proxied}"
            results.append(
                DiagnosticResult(
                    check="connectivity",
                    passed=proxied is not None,
                    message=msg,
                    recommendation="",
                )
            )

            # DNS leak check passes when IPs differ
            leak_ok = proxied != direct
            results.append(
                DiagnosticResult(
                    check="dns_leak",
                    passed=leak_ok,
                    message=msg,
                    recommendation="Check firewall and kill switch settings."
                    if not leak_ok
                    else "",
                )
            )
            return results
        except Exception:
            return [
                DiagnosticResult(
                    check="connectivity",
                    passed=False,
                    message="Connectivity test failed",
                    recommendation="Network error during testing.",
                )
            ]

    def control_api_checks(self, base_url: str) -> list[DiagnosticResult]:
        """Query the control API for service health."""

        import asyncio
        from proxy2vpn.adapters.http_client import GluetunControlClient

        async def _query() -> list[DiagnosticResult]:
            results: list[DiagnosticResult] = []
            async with GluetunControlClient(base_url) as client:
                try:
                    dns = await client.dns_status()
                    ok = dns.status == "running"
                    results.append(
                        DiagnosticResult(
                            check="dns_status",
                            passed=ok,
                            message=f"dns={dns.status}",
                            recommendation="Start DNS service" if not ok else "",
                        )
                    )
                except Exception:
                    results.append(
                        DiagnosticResult(
                            check="dns_status",
                            passed=False,
                            message="dns status unavailable",
                            recommendation="Control server not reachable",
                        )
                    )

                try:
                    upd = await client.updater_status()
                    ok = upd.status in {"completed", "running"}
                    results.append(
                        DiagnosticResult(
                            check="updater_status",
                            passed=ok,
                            message=f"updater={upd.status}",
                            recommendation="Updater not running" if not ok else "",
                        )
                    )
                except Exception:
                    results.append(
                        DiagnosticResult(
                            check="updater_status",
                            passed=False,
                            message="updater status unavailable",
                            recommendation="Control server not reachable",
                        )
                    )

                try:
                    pf = await client.port_forwarded()
                    ok = pf.port > 0
                    results.append(
                        DiagnosticResult(
                            check="port_forward",
                            passed=ok,
                            message=f"port={pf.port}",
                            recommendation="No port forwarded" if not ok else "",
                        )
                    )
                except Exception:
                    results.append(
                        DiagnosticResult(
                            check="port_forward",
                            passed=False,
                            message="port forward unavailable",
                            recommendation="Control server not reachable",
                        )
                    )
            return results

        try:
            return asyncio.run(_query())
        except Exception:
            return [
                DiagnosticResult(
                    check="control_api",
                    passed=False,
                    message="control API check failed",
                    recommendation="Ensure control server is accessible.",
                )
            ]

    def analyze(
        self, log_lines: Iterable[str], port: int | None = None
    ) -> list[DiagnosticResult]:
        """Analyze logs and optionally test connectivity."""
        results = self.analyze_logs(log_lines)
        if port:
            results.extend(self.check_connectivity(port))
        return results

    def health_score(self, results: Iterable[DiagnosticResult]) -> int:
        """Weighted score: start 100, -50 per persistent fail, -25 per non-persistent fail."""
        score = 100
        for r in results:
            if not r.passed:
                score -= 50 if r.persistent else 25
        return max(0, score)
