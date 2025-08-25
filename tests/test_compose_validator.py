import pathlib
import sys
from textwrap import dedent

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn.adapters import compose_validator
from proxy2vpn.adapters.compose_validator import validate_compose


def _env(tmp_path: pathlib.Path) -> pathlib.Path:
    env = tmp_path / "test.env"
    env.write_text("VAR=1")
    return env


def test_valid_compose(tmp_path):
    env = _env(tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        dedent(
            f"""
            x-vpn-base-test: &vpn-base-test
              image: gluetun
              cap_add:
                - NET_ADMIN
              devices:
                - /dev/net/tun
              env_file: {env}
            services:
              svc:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment:
                  VAR: "1"
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
            """
        )
    )
    assert validate_compose(compose) == []


def test_orphaned_profile(tmp_path):
    env = _env(tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        dedent(
            f"""
            x-vpn-base-test: &vpn-base-test
              image: gluetun
              cap_add: [NET_ADMIN]
              devices: [/dev/net/tun]
              env_file: {env}
            x-vpn-base-unused: &vpn-base-unused
              image: gluetun
              cap_add: [NET_ADMIN]
              devices: [/dev/net/tun]
              env_file: {env}
            services:
              svc:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment: {{VAR: "1"}}
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
            """
        )
    )
    errors = validate_compose(compose)
    assert any("not used" in e for e in errors)


def test_duplicate_ports(tmp_path):
    env = _env(tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        dedent(
            f"""
            x-vpn-base-test: &vpn-base-test
              image: gluetun
              cap_add: [NET_ADMIN]
              devices: [/dev/net/tun]
              env_file: {env}
            services:
              one:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment: {{VAR: "1"}}
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
              two:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment: {{VAR: "1"}}
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
            """
        )
    )
    errors = validate_compose(compose)
    assert any("Duplicate port" in e for e in errors)


def test_missing_profile_field(tmp_path):
    env = _env(tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        dedent(
            f"""
              x-vpn-base-test: &vpn-base-test
                cap_add: [NET_ADMIN]
                devices: [/dev/net/tun]
                env_file: {env}
              services:
                svc:
                  <<: *vpn-base-test
                  ports: ["20000:1194/tcp"]
                  environment: {{VAR: "1"}}
                  labels:
                    vpn.type: vpn
                    vpn.port: "20000"
                    vpn.profile: test
            """
        )
    )
    errors = validate_compose(compose)
    assert any("missing field 'image'" in e for e in errors)


def test_location_validation(tmp_path, monkeypatch):
    env = _env(tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text(
        dedent(
            f"""
            x-vpn-base-test: &vpn-base-test
              image: gluetun
              cap_add: [NET_ADMIN]
              devices: [/dev/net/tun]
              env_file: {env}
            services:
              svc:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment:
                  - VPN_SERVICE_PROVIDER=prov
                  - SERVER_CITIES=Toronto
                  - SERVER_COUNTRIES=CA
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
            """
        )
    )

    class DummyServerManager:
        def update_servers(self):
            self.data = {}
            return self.data

        def validate_location(self, provider, location):
            return location in {"Toronto", "CA", "Toronto,CA"}

    monkeypatch.setattr(
        compose_validator, "ServerManager", lambda: DummyServerManager()
    )
    assert validate_compose(compose) == []

    compose.write_text(
        dedent(
            f"""
            x-vpn-base-test: &vpn-base-test
              image: gluetun
              cap_add: [NET_ADMIN]
              devices: [/dev/net/tun]
              env_file: {env}
            services:
              svc:
                <<: *vpn-base-test
                ports: ["20000:1194/tcp"]
                environment:
                  - VPN_SERVICE_PROVIDER=prov
                  - SERVER_CITIES=Atlantis
                  - SERVER_COUNTRIES=CA
                labels:
                  vpn.type: vpn
                  vpn.port: "20000"
                  vpn.profile: test
            """
        )
    )
    errors = validate_compose(compose)
    assert any("invalid location" in e for e in errors)
