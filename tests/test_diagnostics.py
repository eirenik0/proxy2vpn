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
    def fake_get(url, proxies=None, timeout=5):
        class Resp:
            def __init__(self, text: str) -> None:
                self.text = text

        if proxies:
            return Resp("1.1.1.1")
        return Resp("2.2.2.2")

    monkeypatch.setattr(diagnostics.requests, "get", fake_get)
    analyzer = diagnostics.DiagnosticAnalyzer()
    results = analyzer.check_connectivity(8080)
    assert any(r.check == "dns_leak" and r.passed for r in results)
