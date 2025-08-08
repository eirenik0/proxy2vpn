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
