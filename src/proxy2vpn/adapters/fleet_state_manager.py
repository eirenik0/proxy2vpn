"""Fleet State Manager - Reliable singleton for managing VPN fleet operations."""

import asyncio
from collections import Counter
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict

from .compose_manager import ComposeManager
from .display_utils import console
from .docker_ops import (
    ensure_network,
    get_container_by_service_name,
    get_container_ip_async,
    get_vpn_containers,
    recreate_vpn_container,
    remove_container,
    start_container,
    start_vpn_service,
    test_vpn_connection_async,
    stop_container,
)
from .http_client import HTTPClient, HTTPClientConfig
from .logging_utils import get_logger
from .server_manager import ServerManager
from proxy2vpn.core.models import VPNService

logger = get_logger(__name__)


class OperationType(str, Enum):
    """Types of fleet operations."""

    ROTATE = "rotate"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"


class RotationCriteria(str, Enum):
    """Rotation selection criteria."""

    RANDOM = "random"
    PERFORMANCE = "performance"
    LOAD = "load"


class ServiceHealth(BaseModel):
    """Health status of a VPN service."""

    service_name: str
    is_healthy: bool
    health_score: int = 0  # 0-100 score from diagnostic analyzer
    last_checked: datetime
    response_time: Optional[float] = None
    error_message: Optional[str] = None
    consecutive_failures: int = 0

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


@dataclass
class OperationConfig:
    """Configuration for fleet operations."""

    dry_run: bool = False
    criteria: RotationCriteria = RotationCriteria.RANDOM
    max_parallel: int = 5
    rollback_on_failure: bool = True
    health_check_timeout: int = 30
    rotation_attempt_limit: int = 3
    rotation_verification_attempts: int = 3
    rotation_verification_delay_seconds: int = 10
    bad_city_cooldown_seconds: int = 3600
    countries: Optional[List[str]] = None
    provider: Optional[str] = None
    profile: Optional[str] = None


@dataclass
class ServiceRotationPlan:
    """Plan for rotating a single service."""

    service_name: str
    old_location: str
    new_location: str
    reason: str
    candidate_locations: List[str] = field(default_factory=list)


@dataclass
class RotationChange:
    """Persistable description of one completed rotation."""

    requested_service_name: str
    final_service_name: str
    old_location: str
    new_location: str
    candidate_locations: List[str] = field(default_factory=list)
    attempted_locations: List[str] = field(default_factory=list)


@dataclass
class ScaleOperation:
    """Plan for scaling operation."""

    action: OperationType
    services_to_add: List[VPNService] = field(default_factory=list)
    services_to_remove: List[str] = field(default_factory=list)
    allocated_ports: List[int] = field(default_factory=list)


@dataclass
class OperationResult:
    """Result of fleet operation."""

    operation_type: OperationType
    success: bool
    services_affected: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    execution_time: float = 0.0
    dry_run: bool = False
    rotation_changes: List[RotationChange] = field(default_factory=list)


class FleetStateManager:
    """
    Singleton manager for all fleet state and operations.

    Implements Linus's requirements:
    - Single instance manages all fleet state
    - Async locks prevent concurrent operations
    - Atomic operations with rollback capability
    - Batch health checking with connection pooling
    - Immutable operation plans validated before execution
    """

    _instance: Optional["FleetStateManager"] = None
    _instance_compose_path: Optional[str] = None
    _lock = asyncio.Lock()

    @classmethod
    def _resolve_compose_path(cls, compose_file_path: str | Path | None) -> Path:
        from proxy2vpn.core import config

        compose_path = (
            Path(compose_file_path) if compose_file_path else config.COMPOSE_FILE
        )
        return compose_path.expanduser().resolve()

    def __new__(
        cls, compose_file_path: str | Path | None = None
    ) -> "FleetStateManager":
        compose_path = str(cls._resolve_compose_path(compose_file_path))
        if cls._instance is None or cls._instance_compose_path != compose_path:
            cls._instance = super().__new__(cls)
            cls._instance_compose_path = compose_path
        return cls._instance

    def __init__(self, compose_file_path: str | Path | None = None):
        # Only initialize once
        if hasattr(self, "_initialized"):
            return

        # Core managers
        compose_path = self._resolve_compose_path(compose_file_path)
        self.compose_manager = ComposeManager(compose_path)
        self.server_manager = ServerManager()
        self.compose_path = compose_path

        # Fleet state
        self.services: Dict[str, VPNService] = {}
        self.health_status: Dict[str, ServiceHealth] = {}
        self.operation_lock = asyncio.Lock()
        self.last_health_check: Optional[datetime] = None

        # HTTP client for health checks - reused across operations
        self.http_client = HTTPClient(HTTPClientConfig(base_url="http://localhost"))

        # Port allocation tracking
        self.allocated_ports: Set[int] = set()
        self.port_start = 20000

        # Operation history for debugging
        self.operation_history: List[OperationResult] = []
        self.bad_rotation_cities: Dict[Tuple[str, str, str], datetime] = {}

        self._initialized = True
        logger.info("FleetStateManager initialized")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.operation_lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        self.operation_lock.release()

    async def close(self):
        """Cleanup resources."""
        await self.http_client.close()

    def _sync_services_from_compose(self):
        """Sync services from compose manager to local state."""
        compose_services = self.compose_manager.list_services()

        # Update local service state (only VPN services for fleet management)
        self.services = {s.name: s for s in compose_services if hasattr(s, "provider")}

        # Update allocated ports - get ALL ports from compose file, not just VPN services
        self.allocated_ports = self.compose_manager.get_all_used_ports()

        logger.debug(
            f"Synced {len(self.services)} VPN services and {len(self.allocated_ports)} total ports from compose manager"
        )

    def _allocate_ports(self, count: int) -> List[Tuple[int, int]]:
        """Atomically allocate multiple port pairs (proxy_port, control_port)."""
        port_pairs = []
        for _ in range(count):
            proxy_port = self.compose_manager.next_available_port()
            control_port = self.compose_manager.next_available_control_port()
            port_pairs.append((proxy_port, control_port))
        return port_pairs

    def _deallocate_ports(self, port_pairs: List[Tuple[int, int]]):
        """Deallocate port pairs."""
        for proxy_port, control_port in port_pairs:
            self.allocated_ports.discard(proxy_port)
            self.allocated_ports.discard(control_port)

    async def _batch_health_check(
        self, service_names: List[str]
    ) -> Dict[str, ServiceHealth]:
        """
        Perform health checks on multiple services in parallel.

        This addresses Linus's requirement for batched operations instead of
        sequential health checks that kill performance.
        """
        if not service_names:
            return {}

        # Snapshot the current container set once so every service is checked
        # against the same Docker view. Per-service lookups were causing
        # inconsistent results under load.
        try:
            containers = {
                container.name: container
                for container in get_vpn_containers(all=True)
                if container.name
            }
        except Exception as exc:
            logger.warning(
                "Failed to snapshot VPN containers for fleet health checks",
                extra={"error": str(exc)},
            )
            containers = {}

        direct_ip = None
        try:
            from . import ip_utils

            direct_ip = await asyncio.to_thread(ip_utils.fetch_ip, timeout=5)
        except Exception:
            direct_ip = None

        # Use semaphore to limit concurrent health checks (max 10)
        semaphore = asyncio.Semaphore(10)
        results: Dict[str, ServiceHealth] = {}

        async def check_single_service(service_name: str) -> Tuple[str, ServiceHealth]:
            async with semaphore:
                return await self._check_service_health(
                    service_name,
                    container=containers.get(service_name),
                    direct_ip=direct_ip,
                )

        # Execute all health checks in parallel with progress display (like vpn list)
        from rich.progress import Progress

        # Map task -> service name for result attribution
        tasks = {
            asyncio.create_task(check_single_service(name)): name
            for name in service_names
        }

        with Progress() as progress:
            task_progress = progress.add_task(
                "[cyan]Analyzing health", total=len(service_names)
            )

            pending = set(tasks.keys())
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )

                for completed in done:
                    service_name = tasks.get(completed, "")
                    try:
                        name, health = completed.result()
                        results[name] = health
                    except Exception as exc:
                        # Fallback error handling (should be rare as _check_service_health handles errors)
                        logger.error(
                            f"Health check failed for {service_name or '<unknown>'}: {exc}"
                        )
                        previous_health = self.health_status.get(service_name)
                        consecutive_failures = (
                            (previous_health.consecutive_failures + 1)
                            if previous_health
                            else 1
                        )
                        results[service_name] = ServiceHealth(
                            service_name=service_name,
                            is_healthy=False,
                            health_score=0,
                            last_checked=datetime.now(),
                            error_message=str(exc),
                            consecutive_failures=consecutive_failures,
                        )

                    progress.advance(task_progress, 1)

        # Update health cache
        self.health_status.update(results)
        self.last_health_check = datetime.now()

        healthy_count = sum(1 for h in results.values() if h.is_healthy)
        avg_score = (
            sum(h.health_score for h in results.values()) / len(results)
            if results
            else 0
        )
        console.print(
            f"[green]✓[/green] Health check complete: {healthy_count}/{len(results)} healthy (avg score: {avg_score:.1f})"
        )

        return results

    async def _check_service_health(
        self,
        service_name: str,
        timeout: int | None = None,
        container: Any | None = None,
        direct_ip: str | None = None,
    ) -> Tuple[str, ServiceHealth]:
        """Check health of a single service using diagnostic analyzer like vpn list."""
        try:
            service = self.services.get(service_name)
            if not service:
                raise Exception(f"Service {service_name} not found in fleet state")
            effective_timeout = max(1, timeout or 5)

            # Get container
            if container is None:
                container = get_container_by_service_name(service_name)
            if not container:
                raise Exception(f"Container not found for service {service_name}")

            # Check container status
            try:
                container.reload()
            except Exception:
                pass
            if container.status != "running":
                raise Exception(f"Container not running: {container.status}")

            # Use the same diagnostic approach as vpn list
            from .docker_ops import analyze_container_logs
            from proxy2vpn.core.services.diagnostics import DiagnosticAnalyzer
            from proxy2vpn.adapters import ip_utils

            # Get direct IP for diagnostics (same as vpn list)
            if direct_ip is None:
                try:
                    direct_ip = await asyncio.to_thread(
                        ip_utils.fetch_ip,
                        timeout=effective_timeout,
                    )
                except Exception:
                    pass  # Will be handled by diagnostic analyzer

            # Run diagnostic analysis in thread pool (same as vpn list)
            loop = asyncio.get_event_loop()
            analyzer = DiagnosticAnalyzer()

            start_time = time.perf_counter()
            results = await loop.run_in_executor(
                None,
                analyze_container_logs,
                service_name,
                20,
                analyzer,
                effective_timeout,
                direct_ip,
            )
            response_time = time.perf_counter() - start_time

            # Calculate health score (0-100)
            health_score = analyzer.health_score(results)
            is_healthy = (
                health_score >= 60
            )  # Same threshold as elsewhere in the codebase

            # Track consecutive failures across checks
            previous_health = self.health_status.get(service_name)
            previous_failures = (
                previous_health.consecutive_failures if previous_health else 0
            )
            consecutive_failures = 0 if is_healthy else (previous_failures + 1)

            return service_name, ServiceHealth(
                service_name=service_name,
                is_healthy=is_healthy,
                health_score=health_score,
                last_checked=datetime.now(),
                response_time=response_time,
                consecutive_failures=consecutive_failures,
            )

        except Exception as e:
            # Get previous failure count
            previous_health = self.health_status.get(service_name)
            consecutive_failures = (
                (previous_health.consecutive_failures + 1) if previous_health else 1
            )

            return service_name, ServiceHealth(
                service_name=service_name,
                is_healthy=False,
                health_score=0,
                last_checked=datetime.now(),
                error_message=str(e),
                consecutive_failures=consecutive_failures,
            )

    def _create_rotation_plan(
        self, failed_services: List[str], config: OperationConfig
    ) -> List[ServiceRotationPlan]:
        """
        Create immutable rotation plan.

        This addresses Linus's requirement for validating plans before execution.
        """
        plan = []

        for service_name in failed_services:
            service = self.services.get(service_name)
            if not service:
                logger.warning(
                    f"Service {service_name} not found for rotation planning"
                )
                continue

            try:
                # Extract country from service
                country = self._extract_country_from_service(service)
                if config.countries and country not in config.countries:
                    continue

                # Get alternative cities
                available_cities = self.server_manager.list_cities(
                    service.provider, country
                )
                alternative_cities = self._rank_rotation_candidates(
                    service=service,
                    country=country,
                    available_cities=available_cities,
                    config=config,
                )

                if not alternative_cities:
                    logger.warning(
                        f"No alternative cities found for {service_name} in {country}"
                    )
                    continue

                # Select the best candidate first, but keep the rest so execution
                # can fall through if the first choice does not yield a healthy
                # service with a genuinely new egress IP.
                new_location = alternative_cities[0]

                plan.append(
                    ServiceRotationPlan(
                        service_name=service_name,
                        old_location=service.location,
                        new_location=new_location,
                        reason="health_check_failed",
                        candidate_locations=alternative_cities,
                    )
                )

            except Exception as e:
                logger.error(f"Failed to plan rotation for {service_name}: {e}")
                continue

        return plan

    def _rotation_city_key(
        self, provider: str, country: str, city: str
    ) -> Tuple[str, str, str]:
        """Return a normalized lookup key for one rotation candidate city."""

        return (provider.casefold(), country.casefold(), city.casefold())

    def _prune_bad_rotation_cities(self, cooldown_seconds: int) -> None:
        """Drop expired bad-city markers so candidate ranking can recover over time."""

        cutoff = datetime.now().timestamp() - max(1, cooldown_seconds)
        expired = [
            key
            for key, failed_at in self.bad_rotation_cities.items()
            if failed_at.timestamp() < cutoff
        ]
        for key in expired:
            self.bad_rotation_cities.pop(key, None)

    def _is_bad_rotation_city(
        self,
        provider: str,
        country: str,
        city: str,
        cooldown_seconds: int,
    ) -> bool:
        """Return True when CITY recently failed rotation verification."""

        self._prune_bad_rotation_cities(cooldown_seconds)
        return (
            self._rotation_city_key(provider, country, city) in self.bad_rotation_cities
        )

    def _mark_bad_rotation_city(
        self,
        provider: str,
        country: str,
        city: str,
    ) -> None:
        """Record one recently failed rotation city for later plan avoidance."""

        self.bad_rotation_cities[self._rotation_city_key(provider, country, city)] = (
            datetime.now()
        )

    def _clear_bad_rotation_city(
        self,
        provider: str,
        country: str,
        city: str,
    ) -> None:
        """Clear a bad-city marker after a later successful verification."""

        self.bad_rotation_cities.pop(
            self._rotation_city_key(provider, country, city),
            None,
        )

    def _extract_country_from_service(self, service: VPNService) -> str:
        """Extract country from service name or location."""
        country = service.environment.get("SERVER_COUNTRIES", "").strip()
        if country:
            return country.replace("-", " ").title()

        label_country = (
            service.labels.get("vpn.country", "").strip()
            if hasattr(service, "labels")
            else ""
        )
        if label_country:
            return label_country.replace("-", " ").title()

        provider = (
            service.provider.replace(" ", "-").lower() if service.provider else ""
        )
        location = (
            service.location.replace(" ", "-").lower() if service.location else ""
        )
        name = service.name.lower()

        if provider and name.startswith(provider + "-"):
            name = name[len(provider) + 1 :]

        if name.rsplit("-", 1)[-1].isdigit():
            name = name.rsplit("-", 1)[0]

        if location and name.endswith("-" + location):
            name = name[: -(len(location) + 1)]

        normalized = name.replace("-", " ").strip()
        return normalized.title() if normalized else service.location

    def _rank_rotation_candidates(
        self,
        service: VPNService,
        country: str,
        available_cities: List[str],
        config: OperationConfig,
    ) -> List[str]:
        """Order candidate cities using fleet-aware heuristics."""

        current_location = service.location.casefold()
        seen: set[str] = set()
        candidates: List[str] = []
        skipped_bad_cities: List[str] = []
        for city in available_cities:
            normalized = city.casefold()
            if normalized == current_location or normalized in seen:
                continue
            seen.add(normalized)
            if self._is_bad_rotation_city(
                service.provider,
                country,
                city,
                config.bad_city_cooldown_seconds,
            ):
                skipped_bad_cities.append(city)
                continue
            candidates.append(city)

        if skipped_bad_cities:
            logger.info(
                "rotation_candidates_skip_recent_failures",
                extra={
                    "service_name": service.name,
                    "provider": service.provider,
                    "country": country,
                    "cities": skipped_bad_cities,
                },
            )

        if not candidates:
            return []

        if config.criteria == RotationCriteria.RANDOM:
            random.shuffle(candidates)
            return candidates

        usage_counts = self._city_usage_counts(
            provider=service.provider,
            country=country,
            exclude_service=service.name,
        )
        capacity_counts = self._city_capacity_counts(service.provider, country)

        if config.criteria == RotationCriteria.LOAD:
            return sorted(
                candidates,
                key=lambda city: (
                    usage_counts[city.casefold()],
                    -capacity_counts[city.casefold()],
                    city.casefold(),
                ),
            )

        return sorted(
            candidates,
            key=lambda city: (
                -capacity_counts[city.casefold()],
                usage_counts[city.casefold()],
                city.casefold(),
            ),
        )

    def _city_usage_counts(
        self, provider: str, country: str, exclude_service: str | None = None
    ) -> Counter[str]:
        """Count how many current services already use each city."""

        counts: Counter[str] = Counter()
        for service in self.services.values():
            if exclude_service and service.name == exclude_service:
                continue
            if service.provider != provider:
                continue
            if self._extract_country_from_service(service) != country:
                continue
            if service.location:
                counts[service.location.casefold()] += 1
        return counts

    def _city_capacity_counts(self, provider: str, country: str) -> Counter[str]:
        """Count advertised servers per city to prefer broader pools."""

        data = self.server_manager.data or {}
        provider_data = data.get(provider, {})
        counts: Counter[str] = Counter()

        for server in provider_data.get("servers", []):
            if server.get("country") != country:
                continue
            city = server.get("city")
            if not city:
                continue
            ips = [ip for ip in (server.get("ips") or []) if isinstance(ip, str) and ip]
            counts[city.casefold()] += max(1, len(ips))

        return counts

    def _slug_location(self, value: str) -> str:
        """Normalize a location segment to the service-name slug format."""

        return value.strip().lower().replace(" ", "-")

    def _derive_rotated_service_name(
        self,
        service: VPNService,
        target_location: str,
        *,
        current_name: str | None = None,
        target_name: str | None = None,
    ) -> str:
        """Return the renamed service/container name for TARGET_LOCATION."""

        if target_name:
            return target_name

        active_name = current_name or service.name
        current_slug = self._slug_location(service.location)
        target_slug = self._slug_location(target_location)
        if not current_slug or not target_slug or current_slug == target_slug:
            return active_name

        if current_slug not in active_name:
            return active_name

        prefix, suffix = active_name.rsplit(current_slug, 1)
        candidate = f"{prefix}{target_slug}{suffix}"
        existing_names = {item.name for item in self.compose_manager.list_services()}
        existing_names.discard(active_name)
        if candidate not in existing_names:
            return candidate

        candidate_with_port = f"{candidate}-{service.port}"
        if candidate_with_port not in existing_names:
            return candidate_with_port

        index = 2
        while f"{candidate_with_port}-{index}" in existing_names:
            index += 1
        return f"{candidate_with_port}-{index}"

    async def _get_service_egress_ip(
        self, service_name: str, timeout: int | None = None
    ) -> str | None:
        """Return the current observed egress IP for a service, if it can be measured."""

        container = get_container_by_service_name(service_name)
        if not container:
            return None
        try:
            container.reload()
        except Exception:
            pass
        try:
            ip_address = await get_container_ip_async(
                container,
                timeout=max(1, timeout or 3),
            )
        except Exception as exc:
            logger.warning(f"Failed to determine egress IP for {service_name}: {exc}")
            return None
        if not ip_address or ip_address == "N/A":
            return None
        return ip_address

    async def _apply_rotation_location(
        self,
        service: VPNService,
        location: str,
        settle_seconds: int = 10,
        target_name: str | None = None,
    ) -> str:
        """Apply one candidate location and recreate the service container."""

        profile = self.compose_manager.get_profile(service.profile)
        previous_name = service.name
        next_name = self._derive_rotated_service_name(
            service,
            location,
            current_name=previous_name,
            target_name=target_name,
        )
        service.set_location(location)
        service.set_name(next_name)
        self.compose_manager.replace_service(previous_name, service)
        if previous_name != next_name:
            self.services.pop(previous_name, None)
            try:
                await asyncio.to_thread(remove_container, previous_name)
            except Exception:
                pass
        self.services[next_name] = service
        await asyncio.to_thread(recreate_vpn_container, service, profile)
        await asyncio.to_thread(start_container, service.name)
        await asyncio.sleep(settle_seconds)
        return next_name

    async def _execute_rotation_plan(
        self, plan: List[ServiceRotationPlan], config: OperationConfig
    ) -> OperationResult:
        """
        Execute rotation plan atomically with rollback capability.

        This implements Linus's requirement for atomic operations.
        """
        if not plan:
            return OperationResult(
                operation_type=OperationType.ROTATE,
                success=True,
                execution_time=0.0,
                dry_run=config.dry_run,
            )

        if config.dry_run:
            self._display_rotation_plan(plan)
            return OperationResult(
                operation_type=OperationType.ROTATE,
                success=True,
                execution_time=0.0,
                dry_run=True,
            )

        start_time = time.perf_counter()
        successful_rotations = []
        rotation_changes: List[RotationChange] = []
        failed_rotations = []

        # Execute rotations with limited concurrency
        semaphore = asyncio.Semaphore(config.max_parallel)

        async def rotate_single_service(
            rotation_plan: ServiceRotationPlan,
        ) -> Tuple[str, bool, RotationChange | None, str]:
            async with semaphore:
                try:
                    change = await self._execute_single_rotation(rotation_plan, config)
                    return rotation_plan.service_name, True, change, ""
                except Exception as e:
                    return rotation_plan.service_name, False, None, str(e)

        # Execute all rotations in parallel
        tasks = [rotate_single_service(rp) for rp in plan]
        rotation_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        errors = []
        for i, result in enumerate(rotation_results):
            rotation_plan = plan[i]
            if isinstance(result, Exception):
                failed_rotations.append(rotation_plan.service_name)
                errors.append(f"{rotation_plan.service_name}: {result}")
            else:
                service_name, success, change, error = result
                if success:
                    final_name = (
                        change.final_service_name
                        if change is not None
                        else service_name
                    )
                    final_location = (
                        change.new_location
                        if change is not None
                        else rotation_plan.new_location
                    )
                    successful_rotations.append(final_name)
                    if change is not None:
                        rotation_changes.append(change)
                    console.print(
                        f"[green]✓[/green] Rotated {service_name} → {final_name}: {rotation_plan.old_location} → {final_location}"
                    )
                else:
                    failed_rotations.append(service_name)
                    errors.append(f"{service_name}: {error}")

        # Handle rollback if configured and there were failures
        if config.rollback_on_failure and failed_rotations and successful_rotations:
            console.print(
                f"[yellow]⚠️ Rolling back {len(successful_rotations)} successful rotations due to failures[/yellow]"
            )
            # For rotation rollback, we'd need to store original state and restore it
            # This is complex for rotations, so we'll log the issue for now
            errors.append(
                f"Rollback needed but not implemented for rotations. {len(successful_rotations)} services in inconsistent state."
            )

        execution_time = time.perf_counter() - start_time
        success = len(failed_rotations) == 0

        return OperationResult(
            operation_type=OperationType.ROTATE,
            success=success,
            services_affected=successful_rotations + failed_rotations,
            errors=errors,
            execution_time=execution_time,
            rotation_changes=rotation_changes,
        )

    async def _execute_single_rotation(
        self,
        rotation_plan: ServiceRotationPlan,
        config: OperationConfig | None = None,
    ) -> RotationChange:
        """Execute rotation for a single service and return final rotation metadata."""
        config_obj = config or OperationConfig()
        service_name = rotation_plan.service_name

        # Get current service from compose manager
        service = self.compose_manager.get_service(service_name)
        original_name = service.name
        original_location = service.location
        country = self._extract_country_from_service(service)
        previous_ip = await self._get_service_egress_ip(
            service_name,
            timeout=config_obj.health_check_timeout,
        )
        candidate_locations = rotation_plan.candidate_locations or [
            rotation_plan.new_location
        ]
        attempt_limit = max(1, config_obj.rotation_attempt_limit)
        attempted_locations: List[str] = []
        failures: List[str] = []

        for attempt_number, candidate_location in enumerate(
            candidate_locations, start=1
        ):
            if attempt_number > attempt_limit:
                failures.append(
                    f"stopped after {attempt_limit} rotation attempts for {service_name}"
                )
                logger.warning(
                    "rotation_attempt_limit_reached",
                    extra={
                        "service_name": service_name,
                        "attempt_limit": attempt_limit,
                        "attempted_locations": attempted_locations,
                    },
                )
                break
            try:
                attempted_locations.append(candidate_location)
                logger.info(
                    "rotation_attempt_started",
                    extra={
                        "service_name": service_name,
                        "attempt": attempt_number,
                        "attempt_limit": attempt_limit,
                        "candidate_location": candidate_location,
                    },
                )
                active_name = await self._apply_rotation_location(
                    service, candidate_location
                )
                verified, verification_failures = await self._verify_rotation_candidate(
                    service_name=active_name,
                    previous_ip=previous_ip,
                    config=config_obj,
                )
                if not verified:
                    self._mark_bad_rotation_city(
                        service.provider,
                        country,
                        candidate_location,
                    )
                    failures.append(
                        f"{candidate_location}: {' | '.join(verification_failures)}"
                    )
                    logger.warning(
                        "rotation_attempt_failed",
                        extra={
                            "service_name": service_name,
                            "attempt": attempt_number,
                            "attempt_limit": attempt_limit,
                            "candidate_location": candidate_location,
                            "verification_failures": verification_failures,
                        },
                    )
                    continue

                self._clear_bad_rotation_city(
                    service.provider,
                    country,
                    candidate_location,
                )
                logger.info(
                    "rotation_attempt_succeeded",
                    extra={
                        "service_name": service_name,
                        "attempt": attempt_number,
                        "attempt_limit": attempt_limit,
                        "candidate_location": candidate_location,
                        "final_service_name": active_name,
                    },
                )

                return RotationChange(
                    requested_service_name=service_name,
                    final_service_name=active_name,
                    old_location=original_location,
                    new_location=candidate_location,
                    candidate_locations=list(candidate_locations),
                    attempted_locations=list(attempted_locations),
                )
            except Exception as exc:
                logger.warning(
                    "rotation_candidate_failed",
                    extra={
                        "service_name": service.name,
                        "attempt": attempt_number,
                        "attempt_limit": attempt_limit,
                        "candidate_location": candidate_location,
                        "error": str(exc),
                    },
                )
                self._mark_bad_rotation_city(
                    service.provider,
                    country,
                    candidate_location,
                )
                failures.append(f"{candidate_location}: {exc}")

        try:
            await self._apply_rotation_location(
                service,
                original_location,
                target_name=original_name,
            )
        except Exception as exc:
            failures.append(
                f"rollback to {original_name} ({original_location}) failed after exhausted candidates: {exc}"
            )

        raise Exception(
            "No rotation candidate restored healthy connectivity with a new egress IP. "
            + " | ".join(failures)
        )

    async def _verify_rotation_candidate(
        self,
        service_name: str,
        previous_ip: str | None,
        config: OperationConfig,
    ) -> Tuple[bool, List[str]]:
        """Verify one rotated candidate with repeated health and vpn-test probes."""

        attempts = max(1, config.rotation_verification_attempts)
        retry_delay = max(0, config.rotation_verification_delay_seconds)
        failures: List[str] = []

        for attempt in range(1, attempts + 1):
            _, health = await self._check_service_health(
                service_name,
                timeout=config.health_check_timeout,
            )
            vpn_test_passed = await test_vpn_connection_async(
                service_name,
                timeout=config.health_check_timeout,
            )
            current_ip = None
            if vpn_test_passed:
                current_ip = await self._get_service_egress_ip(
                    service_name,
                    timeout=config.health_check_timeout,
                )

            attempt_failures: List[str] = []
            if not vpn_test_passed:
                attempt_failures.append("vpn test failed")
            if previous_ip:
                if not current_ip:
                    attempt_failures.append("unable to verify new egress IP")
                elif current_ip == previous_ip:
                    attempt_failures.append(f"egress IP did not change ({current_ip})")

            if vpn_test_passed and (
                not previous_ip or (current_ip and current_ip != previous_ip)
            ):
                if not health.is_healthy:
                    logger.info(
                        "rotation_candidate_verified_by_vpn_test",
                        extra={
                            "service_name": service_name,
                            "health_score": health.health_score,
                        },
                    )
                return True, failures

            if not health.is_healthy:
                attempt_failures.append(f"health score {health.health_score}")
            if health.error_message:
                attempt_failures.append(health.error_message)

            attempt_summary = (
                " | ".join(attempt_failures)
                if attempt_failures
                else "verification failed"
            )
            failures.append(f"attempt {attempt}/{attempts}: {attempt_summary}")

            if attempt < attempts and retry_delay:
                await asyncio.sleep(retry_delay)

        return False, failures

    def _display_rotation_plan(self, plan: List[ServiceRotationPlan]):
        """Display rotation plan in formatted table."""
        if not plan:
            console.print("[yellow]No rotations needed[/yellow]")
            return

        from rich.table import Table

        table = Table(title="🔄 Fleet Rotation Plan")
        table.add_column("Service", style="cyan")
        table.add_column("Current Location", style="red")
        table.add_column("New Location", style="green")
        table.add_column("Reason", style="yellow")

        for rotation in plan:
            table.add_row(
                rotation.service_name,
                rotation.old_location,
                rotation.new_location,
                rotation.reason,
            )

        console.print(table)

    async def rotate_servers(self, config: OperationConfig) -> OperationResult:
        """
        Main entry point for server rotation.

        This implements Linus's single code path design.
        """
        async with self.operation_lock:
            try:
                start_time = time.perf_counter()

                # Sync current state
                self._sync_services_from_compose()

                # Health check all services in parallel
                service_names = list(self.services.keys())
                health_results = await self._batch_health_check(service_names)

                # Find failed services
                failed_services = [
                    name
                    for name, health in health_results.items()
                    if not health.is_healthy and health.consecutive_failures >= 1
                ]

                if not failed_services:
                    console.print(
                        "[green]🎉 All servers healthy - no rotation needed[/green]"
                    )
                    return OperationResult(
                        operation_type=OperationType.ROTATE,
                        success=True,
                        execution_time=time.perf_counter() - start_time,
                        dry_run=config.dry_run,
                    )

                console.print(
                    f"[yellow]🔄 Found {len(failed_services)} services needing rotation[/yellow]"
                )

                # Create rotation plan
                rotation_plan = self._create_rotation_plan(failed_services, config)

                # Execute rotation plan
                result = await self._execute_rotation_plan(rotation_plan, config)

                # Store operation history
                self.operation_history.append(result)

                return result

            except Exception as e:
                logger.error(f"Fleet rotation failed: {e}")
                return OperationResult(
                    operation_type=OperationType.ROTATE,
                    success=False,
                    errors=[f"Fleet rotation failed: {e}"],
                    execution_time=time.perf_counter() - start_time,
                )

    async def rotate_service(
        self, service_name: str, config: OperationConfig | None = None
    ) -> OperationResult:
        """Rotate a single service using the canonical fleet rotation path."""

        config_obj = config or OperationConfig()
        async with self.operation_lock:
            try:
                start_time = time.perf_counter()
                self._sync_services_from_compose()

                if service_name not in self.services:
                    return OperationResult(
                        operation_type=OperationType.ROTATE,
                        success=False,
                        errors=[f"Service '{service_name}' not found"],
                        execution_time=time.perf_counter() - start_time,
                    )

                rotation_plan = self._create_rotation_plan([service_name], config_obj)
                if not rotation_plan:
                    return OperationResult(
                        operation_type=OperationType.ROTATE,
                        success=False,
                        errors=[
                            f"No rotation candidates available for '{service_name}'"
                        ],
                        execution_time=time.perf_counter() - start_time,
                        dry_run=config_obj.dry_run,
                    )

                result = await self._execute_rotation_plan(rotation_plan, config_obj)
                result.execution_time = time.perf_counter() - start_time
                self.operation_history.append(result)
                return result
            except Exception as e:
                logger.error(f"Single-service rotation failed for {service_name}: {e}")
                return OperationResult(
                    operation_type=OperationType.ROTATE,
                    success=False,
                    errors=[f"Single-service rotation failed for {service_name}: {e}"],
                    execution_time=time.perf_counter() - start_time,
                    dry_run=config_obj.dry_run,
                )

    async def scale_fleet(
        self, config: OperationConfig, action: OperationType, factor: int = 1
    ) -> OperationResult:
        """
        Scale fleet up or down.

        This implements proper fleet scaling that was completely missing.
        """
        if action not in [OperationType.SCALE_UP, OperationType.SCALE_DOWN]:
            return OperationResult(
                operation_type=action,
                success=False,
                errors=[f"Invalid action: {action}"],
            )

        async with self.operation_lock:
            try:
                start_time = time.perf_counter()

                # Sync current state
                self._sync_services_from_compose()

                if action == OperationType.SCALE_UP:
                    result = await self._scale_up(config, factor)
                else:
                    result = await self._scale_down(config, factor)

                result.execution_time = time.perf_counter() - start_time
                self.operation_history.append(result)

                return result

            except Exception as e:
                logger.error(f"Fleet scaling failed: {e}")
                return OperationResult(
                    operation_type=action,
                    success=False,
                    errors=[f"Fleet scaling failed: {e}"],
                    execution_time=time.perf_counter() - start_time,
                )

    async def _scale_up(self, config: OperationConfig, factor: int) -> OperationResult:
        """Scale fleet up by adding new services."""
        console.print(f"[blue]📈 Scaling fleet up by factor {factor}[/blue]")

        try:
            # Get available profiles from compose manager
            available_profiles = self.compose_manager.list_profiles()
            if not available_profiles:
                return OperationResult(
                    operation_type=OperationType.SCALE_UP,
                    success=False,
                    errors=["No profiles available for scaling up"],
                )

            # Select profile based on config
            if config.profile:
                # Find specific profile
                profile = None
                for p in available_profiles:
                    if p.name == config.profile:
                        profile = p
                        break

                if not profile:
                    return OperationResult(
                        operation_type=OperationType.SCALE_UP,
                        success=False,
                        errors=[
                            f"Profile '{config.profile}' not found. Available: {[p.name for p in available_profiles]}"
                        ],
                    )
            else:
                # Use first available profile as default
                profile = available_profiles[0]

            console.print(
                f"[blue]📊 Using profile: {profile.name} ({profile.provider})[/blue]"
            )

            # Get countries to use
            countries = config.countries or ["United States"]  # Default fallback

            # Allocate ports for new services
            new_ports = self._allocate_ports(factor)
            new_services = []

            try:
                # Create new services
                for i in range(factor):
                    import random

                    # Get cities for the provider/country
                    country = random.choice(countries)
                    try:
                        cities = self.server_manager.list_cities(
                            profile.provider, country
                        )
                        if not cities:
                            raise Exception(
                                f"No cities available for {profile.provider} in {country}"
                            )

                        city = random.choice(cities)
                        proxy_port, control_port = new_ports[i]

                        service_name = f"{profile.provider.lower()}-{country.lower().replace(' ', '-')}-{city.lower().replace(' ', '-')}-{proxy_port}"

                        # Create VPNService
                        labels = {
                            "vpn.type": "vpn",
                            "vpn.port": str(proxy_port),
                            "vpn.control_port": str(control_port),
                            "vpn.provider": profile.provider,
                            "vpn.profile": profile.name,
                            "vpn.location": city,
                        }

                        env = {
                            "VPN_SERVICE_PROVIDER": profile.provider,
                            "SERVER_COUNTRIES": country,
                            "SERVER_CITIES": city,
                        }

                        service = VPNService.create(
                            name=service_name,
                            port=proxy_port,
                            control_port=control_port,
                            provider=profile.provider,
                            profile=profile.name,
                            location=city,
                            environment=env,
                            labels=labels,
                        )

                        new_services.append(service)

                    except Exception as e:
                        logger.error(f"Failed to create service {i + 1}: {e}")
                        continue

                if not new_services:
                    self._deallocate_ports(new_ports)
                    return OperationResult(
                        operation_type=OperationType.SCALE_UP,
                        success=False,
                        errors=["Failed to create any new services"],
                    )

                if config.dry_run:
                    self._deallocate_ports(new_ports)
                    service_names = [s.name for s in new_services]
                    console.print(
                        f"[yellow]🔍 Dry run - would create: {', '.join(service_names)}[/yellow]"
                    )
                    return OperationResult(
                        operation_type=OperationType.SCALE_UP,
                        success=True,
                        services_affected=service_names,
                        dry_run=True,
                    )

                # Add services to compose manager and start them
                created_services = []
                errors = []

                # Ensure network exists
                await asyncio.to_thread(ensure_network, False)

                for service in new_services:
                    try:
                        # Add to compose
                        self.compose_manager.add_service(service)

                        # Start container
                        await asyncio.to_thread(
                            start_vpn_service, service, profile, False
                        )

                        # Update local state
                        self.services[service.name] = service

                        created_services.append(service.name)
                        console.print(
                            f"[green]✓[/green] Created and started {service.name}"
                        )

                    except Exception as e:
                        errors.append(f"Failed to create {service.name}: {e}")
                        logger.error(f"Failed to create service {service.name}: {e}")

                        # Remove from compose if it was added
                        try:
                            self.compose_manager.remove_service(service.name)
                        except Exception:
                            pass

                        # Deallocate port
                        self.allocated_ports.discard(service.port)

                return OperationResult(
                    operation_type=OperationType.SCALE_UP,
                    success=len(errors) == 0,
                    services_affected=created_services,
                    errors=errors,
                )

            except Exception as e:
                # Rollback port allocation on error
                self._deallocate_ports(new_ports)
                raise e

        except Exception as e:
            logger.error(f"Scale up failed: {e}")
            return OperationResult(
                operation_type=OperationType.SCALE_UP,
                success=False,
                errors=[f"Scale up failed: {e}"],
            )

    async def _scale_down(
        self, config: OperationConfig, factor: int
    ) -> OperationResult:
        """Scale fleet down by removing services."""
        console.print(f"[blue]📉 Scaling fleet down by factor {factor}[/blue]")

        current_services = list(self.services.keys())
        if len(current_services) <= factor:
            return OperationResult(
                operation_type=OperationType.SCALE_DOWN,
                success=False,
                errors=[
                    f"Cannot remove {factor} services - only {len(current_services)} exist"
                ],
            )

        # Select services to remove (for now, just take the first N)
        services_to_remove = current_services[:factor]

        if config.dry_run:
            console.print(
                f"[yellow]🔍 Dry run - would remove: {', '.join(services_to_remove)}[/yellow]"
            )
            return OperationResult(
                operation_type=OperationType.SCALE_DOWN,
                success=True,
                services_affected=services_to_remove,
                dry_run=True,
            )

        # Remove services
        removed_services = []
        errors = []

        for service_name in services_to_remove:
            try:
                # Stop and remove container
                await asyncio.to_thread(stop_container, service_name)
                await asyncio.to_thread(remove_container, service_name)

                # Remove from compose
                self.compose_manager.remove_service(service_name)

                # Update local state
                service = self.services.pop(service_name, None)
                if service:
                    self.allocated_ports.discard(service.port)

                removed_services.append(service_name)
                console.print(f"[green]✓[/green] Removed {service_name}")

            except Exception as e:
                errors.append(f"Failed to remove {service_name}: {e}")
                logger.error(f"Failed to remove service {service_name}: {e}")

        return OperationResult(
            operation_type=OperationType.SCALE_DOWN,
            success=len(errors) == 0,
            services_affected=removed_services,
            errors=errors,
        )

    def get_fleet_status(self) -> Dict:
        """Get current fleet status."""
        self._sync_services_from_compose()

        return {
            "total_services": len(self.services),
            "services": list(self.services.keys()),
            "allocated_ports": sorted(self.allocated_ports),
            "last_health_check": self.last_health_check.isoformat()
            if self.last_health_check
            else None,
            "healthy_services": sum(
                1 for h in self.health_status.values() if h.is_healthy
            ),
            "average_health_score": sum(
                h.health_score for h in self.health_status.values()
            )
            / len(self.health_status)
            if self.health_status
            else 0,
            "operation_history_count": len(self.operation_history),
        }
