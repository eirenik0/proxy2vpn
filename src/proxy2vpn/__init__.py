"""proxy2vpn Python package."""

try:
    from importlib.metadata import version

    __version__ = version("proxy2vpn")
except Exception:
    # Fallback when package is not installed (development mode)
    __version__ = "dev"

__all__ = [
    "cli",
    "compose_utils",
    "docker_ops",
    "compose_manager",
    "models",
    "config",
    "server_manager",
    "__version__",
]
