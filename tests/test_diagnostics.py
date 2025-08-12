import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import diagnostics


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
    assert any(r.check == "dns_leak" and r.passed for r in results)


def test_health_score():
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = [
        diagnostics.DiagnosticResult("ok", True, "", ""),
        diagnostics.DiagnosticResult("warn", False, "", "", persistent=False),
        diagnostics.DiagnosticResult("error", False, "", "", persistent=True),
    ]
    assert analyzer.health_score(results) == 25
