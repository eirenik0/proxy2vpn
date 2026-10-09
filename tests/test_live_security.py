"""Opt-in tests against real, isolated, digest-pinned Gluetun containers."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from proxy2vpn.cli import app
from proxy2vpn.core import config, security
from proxy2vpn.adapters.http_client import GluetunControlClient

GLUETUN = "qmcgaw/gluetun:v3.41.1@sha256:1a5bf4b4820a879cdf8d93d7ef0d2d963af56670c9ebff8981860b6804ebc8ab"
PROBE = "curlimages/curl:8.19.0@sha256:c03110c736db81bbe1be0296f1f1608c81b954b01626bdfb0a8f84e5bd00ff3c"
AUTH_NAMES = (str(config.CONTROL_AUTH_CONFIG_FILE), security.CLIENT_AUTH_FILE)


def require(condition, message):
    if not condition:
        pytest.fail(message, pytrace=False)


def prerequisite(reason):
    if os.environ.get("PROXY2VPN_LIVE_STRICT") == "1":
        pytest.fail(f"Required live prerequisite unmet: {reason}", pytrace=False)
    pytest.skip(f"Live prerequisite unmet: {reason}")


def command(args, *, stage, input=None, timeout=180):
    """Never expose Docker logs, environment, subprocess args or response bodies."""
    try:
        result = subprocess.run(
            args, input=input, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.fail(f"Live operation failed: {stage}", pytrace=False)
    if result.returncode:
        pytest.fail(f"Live operation failed: {stage}", pytrace=False)
    return result.stdout


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def private_write(path, text):
    path.write_text(text)
    path.chmod(0o600)


class Lab:
    def __init__(self, root, profile, address):
        self.root, self.address = root, address
        root.mkdir(mode=0o700)
        self.compose = root / "compose.yml"
        self.project = "p2v-live-" + uuid.uuid4().hex[:12]
        self.proxy_port, self.control_port = free_port(), free_port()
        self.network = self.project + "-probe"
        private_write(root / "vpn.env", profile.read_text())
        self.data = {
            "services": {
                "vpn": {
                    "image": GLUETUN,
                    "user": "0:0",
                    "cap_add": ["NET_ADMIN"],
                    "devices": ["/dev/net/tun:/dev/net/tun"],
                    "env_file": ["vpn.env"],
                    "environment": {
                        "HTTPPROXY": "on",
                        "HTTPPROXY_LOG": "off",
                        "HTTPPROXY_USER": "",
                        "HTTPPROXY_PASSWORD": "",
                        "HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE": "{}",
                        "HTTP_CONTROL_SERVER_AUTH_CONFIG_FILEPATH": "/gluetun/auth/config.toml",
                    },
                    "ports": [
                        f"{address}:{self.proxy_port}:8888/tcp",
                        f"127.0.0.1:{self.control_port}:8000/tcp",
                    ],
                    "volumes": [
                        "./control-server-auth.toml:/gluetun/auth/config.toml:ro"
                    ],
                    "labels": {
                        "proxy2vpn.live": self.project,
                        "vpn.type": "live-security",
                        "vpn.port": str(self.proxy_port),
                        "vpn.control_port": str(self.control_port),
                        "vpn.profile": "live",
                        "vpn.provider": "protonvpn",
                        "vpn.location": "live",
                    },
                }
            }
        }
        self.save()

    def create_probe_network(self):
        command(
            [
                "docker",
                "network",
                "create",
                "--label",
                f"proxy2vpn.live={self.project}",
                self.network,
            ],
            stage="create isolated probe bridge",
        )

    def save(self):
        with self.compose.open("w") as stream:
            YAML().dump(self.data, stream)
        self.compose.chmod(0o600)

    def compose_command(self, *args):
        return command(
            ["docker", "compose", "-p", self.project, "-f", str(self.compose), *args],
            stage="isolated Compose " + args[0],
        )

    def recreate(self):
        previous = self.compose_command("ps", "-q", "vpn").strip()
        self.compose_command("up", "-d", "--force-recreate", "--no-deps", "vpn")
        current = self.compose_command("ps", "-q", "vpn").strip()
        require(current and current != previous, "Service was not recreated")

    def close(self):
        failures = []

        def attempt(args, stage):
            try:
                return command(args, stage=stage)
            except (pytest.fail.Exception, OSError):
                failures.append(stage)
                return ""

        try:
            self.compose_command("down", "--volumes", "--remove-orphans")
        except (pytest.fail.Exception, OSError):
            failures.append("Compose down")
        # Discovery/removal failure must not suppress independent cleanup phases.
        owned = attempt(
            ["docker", "ps", "-aq", "--filter", f"label=proxy2vpn.live={self.project}"],
            "find owned test containers",
        ).split()
        if owned:
            attempt(["docker", "rm", "-f", *owned], "remove owned test containers")
        networks = attempt(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ],
            "find owned Compose networks",
        ).split()
        probes = attempt(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=proxy2vpn.live={self.project}",
            ],
            "find owned probe bridges",
        ).split()
        for network in dict.fromkeys(networks + probes):
            attempt(["docker", "network", "rm", network], "remove isolated network")
        require(
            not failures, "Isolated resource cleanup failed: " + "; ".join(failures)
        )

    def raw(self, method, route, auth=None, payload=None):
        headers = {}
        if auth:
            token = base64.b64encode(":".join(auth).encode()).decode()
            headers["Authorization"] = "Basic " + token
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.control_port}{route}",
            headers=headers,
            data=body,
            method=method,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except (OSError, urllib.error.URLError):
            return 0

    def ready(self, auth=None):
        until = time.monotonic() + 180
        while time.monotonic() < until:
            current = self.compose_command("ps", "-q", "vpn").strip()
            healthy = (
                command(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                        current,
                    ],
                    stage="fresh tunnel health probe",
                ).strip()
                == "healthy"
            )
            if healthy and self.raw("GET", "/v1/vpn/status", auth) == 200:
                return
            time.sleep(2)
        pytest.fail("Fresh Gluetun authentication probe timed out", pytrace=False)

    def proxy(self, address, *, remote=False, expect=True):
        args = ["curl", "-q"]
        if remote:
            args = [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                self.network,
                "--name",
                self.project + "-probe-" + uuid.uuid4().hex[:8],
                "--label",
                f"proxy2vpn.live={self.project}",
                PROBE,
                "-q",
            ]
        settings = (
            f'proxy = "http://{address}:{self.proxy_port}"\n'
            'noproxy = ""\nsilent\noutput = "/dev/null"\n'
            "connect-timeout = 5\nmax-time = 20\n"
            'write-out = "%{http_code}"\nurl = "https://www.example.com/"\n'
        )
        # Expected refusal is evaluated by exit status, with a positive control.
        until = time.monotonic() + (180 if expect else 35)
        while True:
            try:
                result = subprocess.run(
                    args + ["--config", "-"],
                    input=settings,
                    capture_output=True,
                    text=True,
                    timeout=35,
                )
            except (OSError, subprocess.TimeoutExpired):
                pytest.fail("Proxy probe execution failed", pytrace=False)
            if not expect:
                require(
                    result.returncode == 7,
                    "Remote localhost exclusion was not established",
                )
                return
            if result.returncode == 0 and result.stdout == "200":
                return
            if time.monotonic() >= until:
                pytest.fail("Actual proxied HTTPS did not become ready", pytrace=False)
            time.sleep(2)

    def secure(self, address, *, replace=False):
        args = [
            "--compose-file",
            str(self.compose),
            "system",
            "secure",
            "--proxy-bind-address",
            address,
        ]
        if replace:
            args.append("--replace-control-auth")
        result = CliRunner().invoke(app, args)
        require(result.exit_code == 0, "Migration command failed")

    def generated(self):
        return security.load_control_auth(
            self.compose, "monitor"
        ), security.load_control_auth(self.compose, "operator")


def cleanup_labs(created, root):
    failures = []
    try:
        for lab in reversed(created):
            try:
                lab.close()
            except (pytest.fail.Exception, OSError):
                failures.append("resources for " + lab.project)
    finally:
        try:
            shutil.rmtree(root)
        except OSError:
            failures.append("private scratch cleanup failed")
    if failures:
        pytest.fail("Cleanup failed: " + "; ".join(failures), pytrace=False)


@pytest.fixture
def labs(tmp_path_factory, record_property):
    if os.environ.get("PROXY2VPN_LIVE_SECURITY") != "1":
        prerequisite("set PROXY2VPN_LIVE_SECURITY=1 to opt in")
    profile = Path(os.environ.get("PROXY2VPN_LIVE_PROFILE", ""))
    if not profile.is_file():
        prerequisite("explicit PROXY2VPN_LIVE_PROFILE file")
    try:
        address = ipaddress.IPv4Address(os.environ.get("PROXY2VPN_LIVE_PRIVATE_IP", ""))
        if not address.is_private or address.is_loopback or address.is_unspecified:
            raise ValueError
    except ValueError:
        prerequisite("explicit usable private host IPv4 address")
    if not shutil.which("docker") or not shutil.which("curl"):
        prerequisite("Docker and host curl executables")
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        prerequisite("reachable Docker daemon within 30 seconds")
    if result.returncode:
        prerequisite("reachable Docker daemon")
    if os.environ.get("GLUETUN_CONTROL_AUTH"):
        prerequisite("unset GLUETUN_CONTROL_AUTH for Compose-scoped client validation")
    for image in (GLUETUN, PROBE):
        command(["docker", "pull", image], stage="pull pinned integration image")
    for label, image in (("gluetun", GLUETUN), ("probe", PROBE)):
        identity = command(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                "{{.Architecture}} {{.Id}}",
            ],
            stage="record pinned image identity",
        ).strip()
        record_property(label + "_image", image + " " + identity)
    root = tmp_path_factory.mktemp("live-security")
    root.chmod(0o700)
    created = []

    def factory():
        lab = Lab(root / str(len(created)), profile, str(address))
        created.append(lab)
        lab.create_probe_network()
        return lab

    try:
        yield factory
    finally:
        cleanup_labs(created, root)


# @lat: [[live-security-tests#Live Security Tests#Generated Roles And Compose Scoping]]
def test_live_generated_roles_and_compose_scoping(labs):
    first, second = labs(), labs()
    for lab in (first, second):
        security.ensure_control_auth(lab.compose)
        lab.recreate()
        monitor, operator = lab.generated()
        lab.ready(monitor)
        for route in (
            "/v1/vpn/status",
            "/v1/dns/status",
            "/v1/updater/status",
            "/v1/publicip/ip",
        ):
            require(
                lab.raw("GET", route, monitor) == 200, "Live security assertion failed"
            )
            for auth in (None, ("wrong", "wrong")):
                require(
                    lab.raw("GET", route, auth) in (401, 403),
                    "Live security assertion failed",
                )
        for route in ("/v1/vpn/status", "/v1/dns/status", "/v1/updater/status"):
            require(
                lab.raw("PUT", route, monitor, {"status": "running"}) in (401, 403),
                "Live security assertion failed",
            )
            for auth in (None, ("wrong", "wrong")):
                require(
                    lab.raw("PUT", route, auth, {"status": "running"}) in (401, 403),
                    f"Unauthenticated mutation {route} was not denied",
                )
            require(
                lab.raw("PUT", route, operator, {"status": "running"}) == 200,
                "Live security assertion failed",
            )
        for auth in (monitor, operator):
            for method, route in (
                ("GET", "/v1/vpn/settings"),
                ("GET", "/v1/openvpn/settings"),
                ("GET", "/openvpn/settings"),
                ("PUT", "/v1/vpn/settings"),
            ):
                require(
                    lab.raw(method, route, auth, {} if method == "PUT" else None)
                    in (401, 403),
                    f"{method} {route} was not denied",
                )
        lab.proxy(lab.address)

    async def scoped(lab):
        async with GluetunControlClient(
            f"http://127.0.0.1:{lab.control_port}",
            compose_file=lab.compose,
            retry_attempts=0,
        ) as client:
            await client.get("/v1/vpn/status")
            await client.request("PUT", "/v1/vpn/status", json={"status": "running"})

    for lab in (first, second):
        asyncio.run(scoped(lab))
    require(
        first.raw("GET", "/v1/vpn/status", second.generated()[0]) in (401, 403),
        "Live security assertion failed",
    )
    require(
        second.raw("PUT", "/v1/vpn/status", first.generated()[1], {"status": "running"})
        in (401, 403),
        "Live security assertion failed",
    )


# @lat: [[live-security-tests#Live Security Tests#Publication Reachability]]
def test_live_publication_reachability(labs):
    lab = labs()
    security.ensure_control_auth(lab.compose)
    lab.recreate()
    lab.ready(lab.generated()[0])
    # Calibrate the exact remote path before treating refusal as exclusion.
    lab.proxy(lab.address, remote=True)
    lab.secure("127.0.0.1")
    lab.recreate()
    lab.ready(lab.generated()[0])
    lab.proxy("127.0.0.1")
    lab.proxy(lab.address, remote=True, expect=False)
    lab.secure(lab.address)
    lab.recreate()
    lab.ready(lab.generated()[0])
    lab.proxy(lab.address, remote=True)


# @lat: [[live-security-tests#Live Security Tests#Migration Recreation And Rollback]]
@pytest.mark.parametrize("custom", [False, True])
def test_live_migration_recreation_and_rollback(labs, custom):
    lab = labs()
    old_auth = ("legacy", secrets.token_urlsafe(24)) if custom else None
    role = (
        'auth = "none"'
        if not custom
        else f'auth = "basic"\nusername = "{old_auth[0]}"\npassword = "{old_auth[1]}"'
    )
    private_write(
        lab.root / AUTH_NAMES[0],
        '[[roles]]\nname = "legacy"\nroutes = ["GET /v1/vpn/status"]\n' + role + "\n",
    )
    if custom:
        private_write(lab.root / AUTH_NAMES[1], json.dumps({"custom": "preserve"}))
    originals = {
        name: (lab.root / name).read_bytes() if (lab.root / name).exists() else None
        for name in ("compose.yml", *AUTH_NAMES)
    }
    lab.recreate()
    lab.ready(old_auth)
    lab.proxy(lab.address, remote=True)
    lab.secure(lab.address)
    require(
        (lab.root / AUTH_NAMES[0]).read_bytes() == originals[AUTH_NAMES[0]],
        "Live security assertion failed",
    )
    lab.secure("127.0.0.1", replace=True)
    # File preparation must not silently mutate the already-running service.
    require(
        lab.raw("GET", "/v1/vpn/status", old_auth) == 200,
        "Live security assertion failed",
    )
    monitor, _operator = lab.generated()
    require(
        lab.raw("GET", "/v1/vpn/status", monitor) in (401, 403),
        "Live security assertion failed",
    )
    generated = {name: (lab.root / name).read_bytes() for name in AUTH_NAMES}
    backups = {
        name: (lab.root / (name + ".bak")).read_bytes()
        if (lab.root / (name + ".bak")).exists()
        else None
        for name in ("compose.yml", *AUTH_NAMES)
    }
    require(
        backups["compose.yml"] == originals["compose.yml"],
        "Live security assertion failed",
    )
    for name in AUTH_NAMES:
        require(backups[name] == originals[name], "Live security assertion failed")
    lab.secure("127.0.0.1", replace=True)
    require(
        generated == {name: (lab.root / name).read_bytes() for name in AUTH_NAMES},
        "Live security assertion failed",
    )
    require(
        backups
        == {
            name: (lab.root / (name + ".bak")).read_bytes()
            if (lab.root / (name + ".bak")).exists()
            else None
            for name in backups
        },
        "Live security assertion failed",
    )
    lab.recreate()
    lab.ready(monitor)
    require(
        lab.raw("GET", "/v1/vpn/status", old_auth) in (401, 403),
        "Live security assertion failed",
    )
    lab.proxy("127.0.0.1")
    # Restore all three states, including an originally absent client file.
    for name, content in originals.items():
        path = lab.root / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
            path.chmod(0o600)
    lab.recreate()
    lab.ready(old_auth)
    require(
        lab.raw("GET", "/v1/vpn/status", monitor) in (401, 403),
        "Live security assertion failed",
    )
    lab.proxy(lab.address, remote=True)
