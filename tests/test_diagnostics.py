import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.core.services import diagnostics


def test_temporal_analysis():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = ["AUTH_FAILED", "AUTH_FAILED"]
    results = analyzer.analyze_logs(logs)
    auth = next(r for r in results if r.check == "auth_failure")
    assert auth.persistent is True


def test_temporal_analysis_prefers_latest_log_lines():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = ["AUTH_FAILED", "AUTH_FAILED"] + ["Initialization complete"] * 10
    results = analyzer.analyze_logs(logs)
    assert results == [
        diagnostics.DiagnosticResult(
            check="logs",
            passed=True,
            message="No critical log errors",
            recommendation="",
        )
    ]


def test_auth_detection_ignores_authentication_setup_lines_for_route_failures():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = [
        "2026-03-27T12:05:49Z INFO [http server] read 1 roles from authentication file",
        "2026-03-27T12:05:50Z WARN [vpn] restarting VPN because it failed to pass the healthcheck: startup check: all check tries failed: parallel attempt 1/2 failed: dialing: dial tcp4: lookup cloudflare.com: i/o timeout",
        "2026-03-27T12:05:59Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
        "2026-03-27T12:05:59Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
        "2026-03-27T12:06:07Z INFO [vpn] retrying in 15s",
    ]

    results = analyzer.analyze_logs(logs)

    assert all(result.check != "auth_failure" for result in results)
    route_issue = next(r for r in results if r.check == "route_error")
    assert route_issue.passed is False


def test_openvpn_server_selection_failure_is_detected():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = [
        "2026-03-26T21:21:20Z ERROR [vpn] finding a valid server connection: filtering servers: no server found: for VPN openvpn; protocol udp; country united states; city dallas; hostname us10562.nordvpn.com; target ip address 0.0.0.0",
        "2026-03-26T21:21:20Z INFO [vpn] retrying in 15s",
        "2026-03-26T21:21:35Z ERROR [vpn] finding a valid server connection: filtering servers: no server found: for VPN openvpn; protocol udp; country united states; city dallas; hostname us10562.nordvpn.com; target ip address 0.0.0.0",
    ]

    results = analyzer.analyze_logs(logs)
    failure = next(r for r in results if r.check == "config_error")
    assert failure.passed is False
    assert failure.persistent is True
    assert "server matching the configured country/city" in failure.message
    assert "SERVER_COUNTRIES/SERVER_CITIES" in failure.recommendation


def test_openvpn_route_warnings_are_not_misclassified_as_config_errors():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = [
        "2026-03-27T10:19:07Z INFO [openvpn] sitnl_send: rtnl: generic error (-101): Network unreachable",
        "2026-03-27T10:19:07Z ERROR [openvpn] OpenVPN tried to add an IP route which already exists (RTNETLINK answers: File exists)",
        "2026-03-27T10:19:07Z WARN [openvpn] Previous error details: Linux route add command failed: external program exited with error status: 2",
        "2026-03-27T10:19:07Z WARN [openvpn] OpenVPN was configured to add an IPv6 route. However, no IPv6 has been configured for tun0, therefore the route installation may fail or may not work as expected.",
    ]

    results = analyzer.analyze_logs(logs)

    assert all(result.check != "config_error" for result in results)
    route_issue = next(r for r in results if r.check == "route_error")
    assert route_issue.passed is False
    assert "route setup issue" in route_issue.message
    assert "tun0" in route_issue.recommendation


def test_route_error_detection_ignores_non_vpn_logs():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = [
        "2026-03-27T10:19:07Z ERROR [healthcheck] network unreachable while probing upstream route cache",
        "2026-03-27T10:19:07Z WARN [healthcheck] tun0 metric refresh skipped",
    ]

    results = analyzer.analyze_logs(logs)

    assert results == [
        diagnostics.DiagnosticResult(
            check="logs",
            passed=True,
            message="No critical log errors",
            recommendation="",
        )
    ]


def test_tls_detection_ignores_openssl_library_banner():
    analyzer = diagnostics.DiagnosticAnalyzer()
    logs = [
        "2026-03-27T10:56:56Z INFO [openvpn] OpenVPN 2.6.16 aarch64-alpine-linux-musl [SSL (OpenSSL)] [LZO] [LZ4] [EPOLL] [MH/PKTINFO] [AEAD]",
        "2026-03-27T10:56:56Z INFO [openvpn] library versions: OpenSSL 3.5.5 27 Jan 2026, LZO 2.10",
        "2026-03-27T10:56:58Z INFO [openvpn] Initialization Sequence Completed",
    ]

    results = analyzer.analyze_logs(logs)

    assert all(result.check != "tls_error" for result in results)
    assert results == [
        diagnostics.DiagnosticResult(
            check="logs",
            passed=True,
            message="No critical log errors",
            recommendation="",
        )
    ]


def test_connectivity(monkeypatch):
    def fake_fetch_ip(proxies=None, timeout=5):
        if proxies:
            return "1.1.1.1"
        return "2.2.2.2"

    monkeypatch.setattr(diagnostics.ip_utils, "fetch_ip", fake_fetch_ip)
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = analyzer.check_connectivity(8080)
    assert any(r.check == "connectivity" and r.passed for r in results)


def test_connectivity_message_contains_ips(monkeypatch):
    def fake_fetch_ip(proxies=None, timeout=5):
        return "2.2.2.2" if not proxies else "1.1.1.1"

    monkeypatch.setattr(diagnostics.ip_utils, "fetch_ip", fake_fetch_ip)
    analyzer = diagnostics.DiagnosticAnalyzer()
    result = analyzer.check_connectivity(8080)[0]
    assert "real=2.2.2.2" in result.message
    assert "vpn=1.1.1.1" in result.message


def test_connectivity_failure_includes_direct_ip(monkeypatch):
    def fake_fetch_ip(proxies=None, timeout=5):
        if proxies:
            return ""
        return "2.2.2.2"

    monkeypatch.setattr(diagnostics.ip_utils, "fetch_ip", fake_fetch_ip)
    analyzer = diagnostics.DiagnosticAnalyzer()
    result = analyzer.check_connectivity(8080)[0]
    assert "VPN proxy connection failed" in result.message
    assert not result.passed


def test_connectivity_errors_are_redacted(monkeypatch):
    def fake_fetch_ip(proxies=None, timeout=5):
        raise RuntimeError("failed via http://user:pass@localhost:8080/connect")

    monkeypatch.setattr(diagnostics.ip_utils, "fetch_ip", fake_fetch_ip)
    analyzer = diagnostics.DiagnosticAnalyzer()
    result = analyzer.check_connectivity(
        8080, proxy_user="user", proxy_password="pass"
    )[0]
    assert "user:pass" not in result.message
    assert "***:***" in result.message


def test_health_score():
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = [
        diagnostics.DiagnosticResult(
            check="ok", passed=True, message="", recommendation=""
        ),
        diagnostics.DiagnosticResult(
            check="warn", passed=False, message="", recommendation="", persistent=False
        ),
        diagnostics.DiagnosticResult(
            check="error", passed=False, message="", recommendation="", persistent=True
        ),
    ]
    assert analyzer.health_score(results) == 80


def test_health_score_connectivity_failure():
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = [
        diagnostics.DiagnosticResult(
            check="connectivity",
            passed=False,
            message="VPN not working",
            recommendation="Fix VPN",
        ),
        diagnostics.DiagnosticResult(
            check="ok", passed=True, message="", recommendation=""
        ),
    ]
    # Connectivity failure should result in health score 0 regardless of other checks
    assert analyzer.health_score(results) == 0


def test_health_score_prefers_confirmed_connectivity_over_stale_log_errors():
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = [
        diagnostics.DiagnosticResult(
            check="auth_failure",
            passed=False,
            message="Recent authentication failure detected",
            recommendation="Verify credentials",
            persistent=True,
        ),
        diagnostics.DiagnosticResult(
            check="connectivity",
            passed=True,
            message="VPN working: real=2.2.2.2 vpn=1.1.1.1",
            recommendation="",
        ),
    ]

    assert analyzer.health_score(results) == 85
