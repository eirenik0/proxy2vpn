"""Server availability monitoring and rotation system."""

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .display_utils import console
from .http_client import HTTPClient, HTTPClientConfig, HTTPClientError
from .logging_utils import get_logger
from proxy2vpn.core.models import VPNService

logger = get_logger(__name__)


@dataclass
class ServerAvailability:
    """Server availability status"""

    location: str
    provider: str
    is_available: bool
    tested_at: datetime
    response_time: float | None = None
    error_message: str | None = None


@dataclass
class RotationRecord:
    """Record of server rotation"""

    timestamp: datetime
    service_name: str
    old_location: str
    new_location: str
    reason: str


@dataclass
class ServiceRotation:
    """Single service rotation plan"""

    service_name: str
    old_location: str
    new_location: str
    reason: str


@dataclass
class RotationPlan:
    """Complete rotation plan"""

    rotations: list[ServiceRotation] = field(default_factory=list)

    def add_rotation(
        self, service_name: str, old_location: str, new_location: str, reason: str
    ):
        """Add rotation to plan"""
        self.rotations.append(
            ServiceRotation(
                service_name=service_name,
                old_location=old_location,
                new_location=new_location,
                reason=reason,
            )
        )


@dataclass
class RotationResult:
    """Result of rotation operation"""

    rotated: int
    failed: int
    services: list[str]
    dry_run: bool = False


class ServerMonitor:
    """Monitors server availability and manages rotation"""

    def __init__(self, fleet_manager, http_client: HTTPClient | None = None):
        self.fleet_manager = fleet_manager
        self.http_client = http_client or HTTPClient(HTTPClientConfig(base_url=""))
        self.availability_cache: dict[str, ServerAvailability] = {}
        self.rotation_history: list[RotationRecord] = []
        self.failed_servers: dict[str, list[datetime]] = {}  # Track failure history

    async def check_service_health(
        self, service: VPNService, timeout: int = 30
    ) -> bool:
        """Check if a VPN service is healthy"""
        try:
            from .docker_ops import get_container_by_service_name

            # Get container
            container = get_container_by_service_name(service.name)
            if not container:
                logger.warning(f"Container not found for service {service.name}")
                console.print(f"[yellow]⚠️ Container not found:[/yellow] {service.name}")
                return False

            # Check container status
            container.reload()
            if container.status != "running":
                logger.warning(
                    f"Container {service.name} is not running: {container.status}"
                )
                console.print(
                    f"[yellow]⚠️ Container not running:[/yellow] {service.name} ({container.status})"
                )
                return False

            # Test proxy connectivity
            from .docker_ops import _get_authenticated_proxy_url

            proxies = _get_authenticated_proxy_url(container, str(service.port))
            test_url = "http://httpbin.org/ip"

            start_time = time.perf_counter()
            await self.http_client.get(test_url, proxy=proxies["http"], timeout=timeout)
            response_time = time.perf_counter() - start_time

            # Update availability cache
            self.availability_cache[service.location] = ServerAvailability(
                location=service.location,
                provider=service.provider,
                is_available=True,
                tested_at=datetime.now(),
                response_time=response_time,
            )
            return True

        except asyncio.TimeoutError:
            logger.warning(f"Timeout testing service {service.name}")
            console.print(f"[yellow]⏱️ Timeout testing service:[/yellow] {service.name}")
            self._record_failure(service.location)
            return False
        except HTTPClientError as e:
            logger.error(f"Network error testing service {service.name}: {e}")
            self._record_failure(service.location)
            return False
        except Exception as e:
            logger.error(f"Error checking service {service.name}: {e}")
            return False

    def _record_failure(self, location: str):
        """Record server failure for tracking"""
        if location not in self.failed_servers:
            self.failed_servers[location] = []

        self.failed_servers[location].append(datetime.now())

        # Keep only recent failures (last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self.failed_servers[location] = [
            failure_time
            for failure_time in self.failed_servers[location]
            if failure_time > cutoff
        ]

    def _is_recently_failed(self, location: str, hours: int = 2) -> bool:
        """Check if server failed recently"""
        if location not in self.failed_servers:
            return False

        cutoff = datetime.now() - timedelta(hours=hours)
        recent_failures = [
            failure_time
            for failure_time in self.failed_servers[location]
            if failure_time > cutoff
        ]

        return len(recent_failures) > 0

    async def check_fleet_health(self) -> dict[str, bool]:
        """Check health of all VPN services in fleet"""
        services = self.fleet_manager.compose_manager.list_services()
        vpn_services = [s for s in services if hasattr(s, "provider")]

        health_results = {}

        # Use semaphore to limit concurrent health checks
        semaphore = asyncio.Semaphore(10)

        async def check_service_with_semaphore(service: VPNService):
            async with semaphore:
                try:
                    is_healthy = await self.check_service_health(service)
                    health_results[service.name] = is_healthy

                    if is_healthy:
                        console.print(
                            f"[green]✅ {service.name} ({service.location}) - Healthy[/green]"
                        )
                    else:
                        console.print(
                            f"[red]❌ {service.name} ({service.location}) - Unhealthy[/red]"
                        )

                except Exception as e:
                    logger.error(f"Health check failed for {service.name}: {e}")
                    health_results[service.name] = False
                    console.print(
                        f"[red]❌ {service.name} ({service.location}) - Error: {e}[/red]"
                    )

        # Run health checks in parallel
        tasks = [check_service_with_semaphore(service) for service in vpn_services]
        await asyncio.gather(*tasks, return_exceptions=True)

        return health_results

    async def rotate_failed_servers(self, dry_run: bool = False) -> RotationResult:
        """Rotate servers that are failing or unavailable"""
        console.print("[yellow]🔍 Checking server health across fleet...[/yellow]")

        # Check health of all services
        health_results = await self.check_fleet_health()

        # Find failed services
        failed_services = []
        services = self.fleet_manager.compose_manager.list_services()

        for service in services:
            if hasattr(service, "provider") and not health_results.get(
                service.name, True
            ):
                failed_services.append(service)

        if not failed_services:
            console.print("[green]🎉 All servers healthy - no rotation needed[/green]")
            return RotationResult(rotated=0, failed=0, services=[])

        console.print(
            f"[yellow]🔄 Found {len(failed_services)} services needing rotation[/yellow]"
        )

        # Generate rotation plan
        rotation_plan = await self._generate_rotation_plan(failed_services)

        if dry_run:
            self._display_rotation_plan(rotation_plan)
            return RotationResult(rotated=0, failed=0, services=[], dry_run=True)

        # Execute rotations
        rotated_count = 0
        failed_count = 0

        for rotation in rotation_plan.rotations:
            try:
                console.print(
                    f"[blue]🔄 Rotating {rotation.service_name}: {rotation.old_location} → {rotation.new_location}[/blue]"
                )

                await self._execute_service_rotation(rotation)
                rotated_count += 1

                # Record rotation history
                self.rotation_history.append(
                    RotationRecord(
                        timestamp=datetime.now(),
                        service_name=rotation.service_name,
                        old_location=rotation.old_location,
                        new_location=rotation.new_location,
                        reason=rotation.reason,
                    )
                )

            except Exception as e:
                logger.error(f"Failed to rotate {rotation.service_name}: {e}")
                console.print(
                    f"[red]❌ Failed to rotate {rotation.service_name}: {e}[/red]"
                )
                failed_count += 1

        console.print(
            f"[green]✅ Rotation complete: {rotated_count} rotated, {failed_count} failed[/green]"
        )

        return RotationResult(
            rotated=rotated_count,
            failed=failed_count,
            services=[r.service_name for r in rotation_plan.rotations],
        )

    async def _generate_rotation_plan(
        self, failed_services: list[VPNService]
    ) -> RotationPlan:
        """Generate an intelligent rotation plan for failed services"""
        plan = RotationPlan()

        for service in failed_services:
            try:
                # Extract country from service location or use location as country
                country = self._extract_country_from_service(service)

                # Get alternative servers in same country
                available_cities = self.fleet_manager.server_manager.list_cities(
                    service.provider, country
                )

                # Filter out current location and recently failed locations
                alternative_cities = [
                    city
                    for city in available_cities
                    if city != service.location and not self._is_recently_failed(city)
                ]

                if not alternative_cities:
                    logger.warning(
                        f"No alternative servers for {service.name} in {country}"
                    )
                    continue

                # Choose best alternative (random for now, could be smarter)
                new_location = random.choice(alternative_cities)

                plan.add_rotation(
                    service_name=service.name,
                    old_location=service.location,
                    new_location=new_location,
                    reason="health_check_failed",
                )

            except Exception as e:
                logger.error(f"Failed to plan rotation for {service.name}: {e}")
                continue

        return plan

    def _extract_country_from_service(self, service: VPNService) -> str:
        """Extract country from service name or location"""
        # Try to extract country from service name if it follows naming convention
        # Format: provider-country-city
        name_parts = service.name.split("-")
        if len(name_parts) >= 3:
            country = name_parts[1].replace("-", " ").title()
            return country

        # Fallback: use location as country (works for country-level locations)
        return service.location

    async def _execute_service_rotation(self, rotation: ServiceRotation):
        """Execute server rotation for a single service"""
        # Update service configuration with new location
        compose_manager = self.fleet_manager.compose_manager

        # Get current service
        service = compose_manager.get_service(rotation.service_name)

        # Update location and environment
        service.location = rotation.new_location
        service.environment["SERVER_CITIES"] = rotation.new_location
        service.labels["vpn.location"] = rotation.new_location

        # Save updated service to compose file
        compose_manager.update_service(service)

        # Recreate container with new configuration
        from .docker_ops import recreate_vpn_container, start_container

        profile = compose_manager.get_profile(service.profile)
        await asyncio.to_thread(recreate_vpn_container, service, profile)
        await asyncio.to_thread(start_container, service.name)

        # Wait for container to stabilize
        await asyncio.sleep(15)

        # Verify new connection is working
        is_healthy = await self.check_service_health(service)
        if not is_healthy:
            raise Exception(f"Service {service.name} still unhealthy after rotation")

    def _display_rotation_plan(self, plan: RotationPlan):
        """Display rotation plan in a formatted table"""
        if not plan.rotations:
            console.print("[yellow]No rotations needed[/yellow]")
            return

        from rich.table import Table

        table = Table(title="🔄 Server Rotation Plan")
        table.add_column("Service", style="cyan")
        table.add_column("Current Location", style="red")
        table.add_column("New Location", style="green")
        table.add_column("Reason", style="yellow")

        for rotation in plan.rotations:
            table.add_row(
                rotation.service_name,
                rotation.old_location,
                rotation.new_location,
                rotation.reason,
            )

        console.print(table)

    def get_rotation_history(self, hours: int = 24) -> list[RotationRecord]:
        """Get rotation history for specified time period"""
        cutoff = datetime.now() - timedelta(hours=hours)
        return [record for record in self.rotation_history if record.timestamp > cutoff]

    def get_server_failure_stats(self) -> dict[str, int]:
        """Get failure statistics by server location"""
        stats = {}
        for location, failures in self.failed_servers.items():
            # Count failures in last 24 hours
            recent_failures = [
                f for f in failures if f > datetime.now() - timedelta(hours=24)
            ]
            stats[location] = len(recent_failures)

        return stats
