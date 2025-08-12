import asyncio
import pathlib
import sys

import pytest

# Ensure src package is importable
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from proxy2vpn import ip_utils
from proxy2vpn import typer_ext


def test_fetch_ip_disallows_async_use():
    async def runner():
        with pytest.raises(RuntimeError):
            ip_utils.fetch_ip()

    asyncio.run(runner())


def test_run_async_disallows_nested_event_loop():
    async def noop() -> None:
        pass

    wrapped = typer_ext.run_async(noop)

    async def runner():
        with pytest.raises(RuntimeError):
            wrapped()

    asyncio.run(runner())
