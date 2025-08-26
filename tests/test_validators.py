from pathlib import Path

import pytest
import typer

from proxy2vpn.adapters.validators import sanitize_name, sanitize_path, validate_port


def test_validate_port_bounds():
    assert validate_port(8080) == 8080
    with pytest.raises(typer.BadParameter):
        validate_port(70000)


def test_sanitize_name():
    assert sanitize_name("good-name") == "good-name"
    with pytest.raises(typer.BadParameter):
        sanitize_name("bad name!")


def test_sanitize_path(tmp_path):
    p = tmp_path / "file"
    resolved = sanitize_path(Path(str(p)))
    assert resolved.is_absolute()
