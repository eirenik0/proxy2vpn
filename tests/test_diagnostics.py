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
