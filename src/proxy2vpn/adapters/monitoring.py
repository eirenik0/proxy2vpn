"""Monitoring utilities for proxy2vpn."""

from typing import Any

import psutil

from .docker_ops import get_container_diagnostics, get_vpn_containers
from .logging_utils import get_logger

logger = get_logger(__name__)


def monitor_vpn_health() -> list[dict[str, Any]]:
    """Return diagnostic details for all VPN containers."""

    diagnostics: list[dict[str, Any]] = []
    try:
        containers = get_vpn_containers(all=False)
    except RuntimeError:
        return diagnostics
    for container in containers:
        try:
            diag = get_container_diagnostics(container)
            diagnostics.append(diag)
            # Avoid clashing with logging.LogRecord reserved attributes (e.g., 'name')
            safe_extra = dict(diag)
            if "name" in safe_extra:
                safe_extra["container_name"] = safe_extra.pop("name")
            logger.info("container_health", extra=safe_extra)
        except RuntimeError as exc:  # pragma: no cover - rare error path
            logger.error(
                "container_diagnostic_failed",
                extra={"container_name": container.name, "error": str(exc)},
            )
    return diagnostics


def collect_system_metrics() -> dict[str, float]:
    """Return basic CPU and memory metrics for the host system."""
    metrics = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
    }
    logger.info("system_metrics", extra=metrics)
    return metrics
