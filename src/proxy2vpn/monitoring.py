"""Monitoring utilities for proxy2vpn."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import psutil

from .docker_ops import get_container_diagnostics, get_vpn_containers

logger = logging.getLogger(__name__)


def monitor_vpn_health() -> List[Dict[str, Any]]:
    """Return diagnostic details for all VPN containers."""

    diagnostics: List[Dict[str, Any]] = []
    try:
        containers = get_vpn_containers(all=False)
    except RuntimeError:
        return diagnostics
    for container in containers:
        try:
            diag = get_container_diagnostics(container)
            diagnostics.append(diag)
            logger.info("container_health", extra=diag)
        except RuntimeError as exc:  # pragma: no cover - rare error path
            logger.error(
                "container_diagnostic_failed",
                extra={"name": container.name, "error": str(exc)},
            )
    return diagnostics


def collect_system_metrics() -> Dict[str, float]:
    """Return basic CPU and memory metrics for the host system."""
    metrics = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
    }
    logger.info("system_metrics", extra=metrics)
    return metrics
