import pathlib
import sys

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import compose_utils


def test_set_service_image(tmp_path):
    compose_src = pathlib.Path(__file__).parent / "test_compose.yml"
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(compose_src.read_text())

    compose_utils.set_service_image(compose_path, "testvpn1", "custom/image:latest")

    data = compose_utils.load_compose(compose_path)
    assert data["services"]["testvpn1"]["image"] == "custom/image:latest"


def test_parse_env_with_issues():
    parsed, issues = compose_utils.parse_env_with_issues(
        {"A": "1", "B": "2", "C": None}
    )
    assert parsed == {"A": "1", "B": "2", "C": "None"}
    assert issues == []

    parsed, issues = compose_utils.parse_env_with_issues(
        ["A=1", "BROKEN", {"bad": "entry"}]
    )
    assert parsed == {"A": "1"}
    assert issues == [
        "invalid environment entry: 'BROKEN'",
        "invalid environment entry: {'bad': 'entry'}",
    ]


def test_iter_port_mappings_with_issues():
    parsed, issues = compose_utils.iter_port_mappings_with_issues(
        ["20000:1194/tcp", "x:y"]
    )
    assert parsed == [(20000, 1194)]
    assert issues == ["invalid port mapping: 'x:y'"]
