from proxy2vpn.adapters.http_client_config import GluetunControlSettings


def test_gluetun_control_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "user:pass")

    settings = GluetunControlSettings()

    assert settings.control_auth == "user:pass"
    assert settings.auth_tuple() == ("user", "pass")


def test_gluetun_control_settings_rejects_invalid_auth(monkeypatch):
    monkeypatch.setenv("GLUETUN_CONTROL_AUTH", "no_colon")

    settings = GluetunControlSettings()

    try:
        settings.auth_tuple()
    except ValueError as exc:
        assert "GLUETUN_CONTROL_AUTH" in str(exc)
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected invalid auth to raise ValueError")
