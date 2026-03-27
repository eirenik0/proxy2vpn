"""Diagnostic analysis for proxy2vpn containers."""

from __future__ import annotations

import re
from typing import Iterable
from pydantic import BaseModel, ConfigDict

from proxy2vpn.adapters import ip_utils
from proxy2vpn.adapters.proxy_utils import build_proxy_urls
from proxy2vpn.adapters.proxy_utils import redact_proxy_url


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

        # Docker returns tailed logs oldest-to-newest, so inspect the newest slice.
        recent_lines = lines[-10:] if len(lines) > 10 else lines
        recent_text = " ".join(recent_lines).lower()

        server_selection_failure = self._detect_server_selection_failure(
            recent_lines, recent_text
        )
        if server_selection_failure is not None:
            return [server_selection_failure]

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

        route_issue = self._detect_route_setup_issue(recent_lines, recent_text)
        if route_issue is not None:
            return [route_issue]

        # Configuration issues in recent logs
        if re.search(r"\b(config|configuration)\b", recent_text) and any(
            re.search(rf"\b{word}\b", recent_text)
            for word in ("error", "invalid", "missing")
        ):
            return [
                DiagnosticResult(
                    check="config_error",
                    passed=False,
                    message="Recent configuration issue detected",
                    recommendation="Verify profile env file and service settings.",
                    persistent=True,
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

    def _detect_server_selection_failure(
        self, recent_lines: list[str], recent_text: str
    ) -> DiagnosticResult | None:
        """Detect OpenVPN server selection failures caused by missing matching servers."""

        markers = (
            "finding a valid server connection",
            "filtering servers",
            "no server found",
        )
        if not any(marker in recent_text for marker in markers):
            return None
        if "openvpn" not in recent_text and "vpn" not in recent_text:
            return None

        failure_count = sum(
            (
                "no server found" in line.lower()
                or "finding a valid server connection" in line.lower()
            )
            for line in recent_lines
        )
        persistent = failure_count >= 2 or "retrying in" in recent_text
        return DiagnosticResult(
            check="config_error",
            passed=False,
            message="OpenVPN could not find a server matching the configured country/city.",
            recommendation=(
                "Verify SERVER_COUNTRIES/SERVER_CITIES, provider hostname, and "
                "refresh the provider server list."
            ),
            persistent=persistent,
        )

    def _detect_route_setup_issue(
        self, recent_lines: list[str], recent_text: str
    ) -> DiagnosticResult | None:
        """Detect route installation problems without misclassifying them as config."""

        markers = (
            "rtnetlink answers: file exists",
            "linux route add command failed",
            "route installation may fail",
            "network unreachable",
        )
        if not any(marker in recent_text for marker in markers):
            return None
        if "route" not in recent_text and "tun0" not in recent_text:
            return None

        failure_count = sum(
            any(marker in line.lower() for marker in markers) for line in recent_lines
        )
        return DiagnosticResult(
            check="route_error",
            passed=False,
            message="Recent OpenVPN route setup issue detected",
            recommendation=(
                "Inspect duplicate or stale routes on tun0, IPv6 route injection, "
                "and recreate the container if the proxy stays unreachable."
            ),
            persistent=failure_count >= 2,
        )

    def check_connectivity(
        self,
        port: int,
        proxy_user: str | None = None,
        proxy_password: str | None = None,
        timeout: int = 5,
        direct_ip: str | None = None,
    ) -> list[DiagnosticResult]:
        """Connectivity + DNS leak checks with HTTP proxy authentication support."""
        proxies = build_proxy_urls(port, username=proxy_user, password=proxy_password)

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
            message = redact_proxy_url(str(e))
            return [
                DiagnosticResult(
                    check="connectivity",
                    passed=False,
                    message=f"Connectivity test failed: {message}",
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
        results_list = list(results)

        # If connectivity fails, container is completely useless
        connectivity_results = [r for r in results_list if r.check == "connectivity"]
        for r in connectivity_results:
            if r.check == "connectivity" and not r.passed:
                return 0
        connectivity_passed = any(r.passed for r in connectivity_results)

        # If DNS or authentication fails persistently, container is broken
        critical_failures = ["dns_status", "auth_failure", "config_error"]
        critical_results = [
            r for r in results_list if r.check in critical_failures and not r.passed
        ]

        # A confirmed proxied IP is stronger evidence than stale or transient logs.
        if connectivity_passed:
            penalty = (len(critical_results) * 15) + (
                sum(
                    1
                    for r in results_list
                    if not r.passed
                    and r.check not in critical_failures
                    and r.check != "connectivity"
                )
                * 5
            )
            return max(70, 100 - penalty)

        for r in critical_results:
            if r.check in critical_failures and not r.passed:
                return 0 if r.persistent else 25

        # Minor issues don't matter if core functionality works
        minor_issues = sum(
            1 for r in results_list if not r.passed and r.check not in critical_failures
        )
        return max(50, 100 - (minor_issues * 10))
