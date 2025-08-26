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
        """Recent log analysis - focus on latest logs to avoid outdated issues."""
        lines = [str(line) for line in log_lines]

        # Only analyze most recent logs (first 10 lines of recent logs)
        recent_lines = lines[:10] if len(lines) > 10 else lines
        recent_text = " ".join(recent_lines).lower()

        # Authentication failures in recent logs only
        recent_auth_failures = sum(
            "auth_failed" in line.lower() for line in recent_lines
        )
        if recent_auth_failures > 0 or (
            "auth" in recent_text and "fail" in recent_text
        ):
            persistent = recent_auth_failures >= 2
            return [
                DiagnosticResult(
                    check="auth_failure",
                    passed=False,
                    message="Recent authentication failure detected",
                    recommendation="Verify credentials and provider configuration.",
                    persistent=persistent,
                )
            ]

        # TLS issues in recent logs
        if "tls" in recent_text or "certificate" in recent_text or "ssl" in recent_text:
            return [
                DiagnosticResult(
                    check="tls_error",
                    passed=False,
                    message="Recent TLS or certificate issue detected",
                    recommendation="Check certificates and TLS settings.",
                )
            ]

        # DNS issues in recent logs
        if "dns" in recent_text and "fail" in recent_text:
            return [
                DiagnosticResult(
                    check="dns_error",
                    passed=False,
                    message="Recent DNS resolution failure detected",
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

    def check_connectivity(
        self,
        port: int,
        proxy_user: str | None = None,
        proxy_password: str | None = None,
        timeout: int = 5,
        direct_ip: str | None = None,
    ) -> list[DiagnosticResult]:
        """Connectivity + DNS leak checks with HTTP proxy authentication support."""
        # Build proxy URL with authentication if provided
        if proxy_user and proxy_password:
            proxy_url = f"http://{proxy_user}:{proxy_password}@localhost:{port}"
        else:
            proxy_url = f"http://localhost:{port}"

        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

        try:
            # Use pre-fetched direct IP if provided, otherwise fetch it
            if direct_ip:
                direct = direct_ip
            else:
                direct = ip_utils.fetch_ip(timeout=timeout)

            if not direct:
                return [
                    DiagnosticResult(
                        check="connectivity",
                        passed=False,
                        message="No internet connection",
                        recommendation="Check network connectivity",
                    )
                ]

            # Test proxy connection - this is the critical test
            proxied = ip_utils.fetch_ip(proxies=proxies, timeout=timeout)

            # If proxy connection fails, container is broken
            if not proxied:
                return [
                    DiagnosticResult(
                        check="connectivity",
                        passed=False,
                        message="VPN proxy connection failed",
                        recommendation="VPN container is not responding - check container status and port accessibility",
                    )
                ]

            # If proxy returns same IP as direct, VPN is not working
            if proxied == direct:
                return [
                    DiagnosticResult(
                        check="connectivity",
                        passed=False,
                        message=f"VPN not working - still showing real IP {direct}",
                        recommendation="VPN tunnel is down - check VPN container logs and configuration",
                    )
                ]

            # Success case - VPN is working properly
            return [
                DiagnosticResult(
                    check="connectivity",
                    passed=True,
                    message=f"VPN working: real={direct} vpn={proxied}",
                    recommendation="",
                )
            ]

        except Exception as e:
            return [
                DiagnosticResult(
                    check="connectivity",
                    passed=False,
                    message=f"Connectivity test failed: {e}",
                    recommendation="Check if container port {port} is accessible",
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
        self,
        log_lines: Iterable[str],
        port: int | None = None,
        proxy_user: str | None = None,
        proxy_password: str | None = None,
        timeout: int = 5,
        direct_ip: str | None = None,
    ) -> list[DiagnosticResult]:
        """Analyze logs and optionally test connectivity."""
        results = self.analyze_logs(log_lines)
        if port:
            results.extend(
                self.check_connectivity(
                    port, proxy_user, proxy_password, timeout, direct_ip
                )
            )
        return results

    def health_score(self, results: Iterable[DiagnosticResult]) -> int:
        """Reality-based health scoring: broken containers get 0, working containers get high scores."""
        # If connectivity fails, container is completely useless
        for r in results:
            if r.check == "connectivity" and not r.passed:
                return 0

        # If DNS or authentication fails persistently, container is broken
        critical_failures = ["dns_status", "auth_failure"]
        for r in results:
            if r.check in critical_failures and not r.passed:
                return 0 if r.persistent else 25

        # Minor issues don't matter if core functionality works
        minor_issues = sum(
            1 for r in results if not r.passed and r.check not in critical_failures
        )
        return max(50, 100 - (minor_issues * 10))
