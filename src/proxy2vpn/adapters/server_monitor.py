"""Server availability monitoring and rotation system."""

import asyncio
import random
from datetime import datetime, timedelta
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .display_utils import console
from .http_client import HTTPClient, HTTPClientConfig
from .logging_utils import get_logger
from proxy2vpn.core.services.health_assessment import (
    HealthAssessment,
    HealthAssessmentService,
)
from proxy2vpn.core.models import VPNService

logger = get_logger(__name__)


class ServerAvailability(BaseModel):
    """Server availability status"""

    location: str
    provider: str
    is_available: bool
    tested_at: datetime
    response_time: float | None = None
    error_message: str | None = None

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    @field_validator("response_time")
    @classmethod
    def _non_negative_response_time(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("response_time must be >= 0")
        return v


class RotationRecord(BaseModel):
    """Record of server rotation"""

    timestamp: datetime
    service_name: str
    old_location: str
    new_location: str
    reason: str

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class ServiceRotation(BaseModel):
    """Single service rotation plan"""

    service_name: str
    old_location: str
    new_location: str
    reason: str

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class RotationPlan(BaseModel):
    """Complete rotation plan"""

    rotations: list[ServiceRotation] = Field(default_factory=list)

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

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


class RotationResult(BaseModel):
    """Result of rotation operation"""

    rotated: int
    failed: int
    services: list[str]
    dry_run: bool = False

    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class ServerMonitor:
    """Monitors server availability and manages rotation"""

    def __init__(self, fleet_manager, http_client: HTTPClient | None = None):
        self.fleet_manager = fleet_manager
        self.http_client = http_client or HTTPClient(
            HTTPClientConfig(base_url="http://localhost")
        )
        self.assessor = HealthAssessmentService()
        self.availability_cache: dict[str, ServerAvailability] = {}
        self.rotation_history: list[RotationRecord] = []
        self.failed_servers: dict[str, list[datetime]] = {}  # Track failure history
        self.last_assessments: dict[str, HealthAssessment] = {}

    async def check_service_health(
        self, service: VPNService, timeout: int = 30
    ) -> bool:
        """Check if a VPN service is healthy"""
        try:
            assessment = await self.assessor.assess_service(service, timeout=timeout)
            self.last_assessments[service.name] = assessment
            self.availability_cache[service.location] = ServerAvailability(
                location=service.location,
                provider=service.provider,
                is_available=assessment.health_score >= self.assessor.threshold,
                tested_at=datetime.now(),
                response_time=None,
                error_message=(
                    None
                    if assessment.health_score >= self.assessor.threshold
                    else ", ".join(assessment.failing_checks) or assessment.health_class
                ),
            )
            return assessment.health_score >= self.assessor.threshold

        except asyncio.TimeoutError:
            logger.warning(f"Timeout testing service {service.name}")
            console.print(f"[yellow]⏱️ Timeout testing service:[/yellow] {service.name}")
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

        assessments = await self.assessor.assess_services(vpn_services)
        self.last_assessments = assessments
        health_results: dict[str, bool] = {}
        for service in vpn_services:
            assessment = assessments[service.name]
            is_healthy = assessment.health_score >= self.assessor.threshold
            health_results[service.name] = is_healthy
            if is_healthy:
                console.print(
                    f"[green]✅ {service.name} ({service.location}) - Healthy ({assessment.health_score}/100)[/green]"
                )
            else:
                console.print(
                    f"[red]❌ {service.name} ({service.location}) - {assessment.health_class} ({assessment.health_score}/100)[/red]"
                )

        return health_results

    async def rotate_failed_servers(self, dry_run: bool = False) -> RotationResult:
        """Compatibility shim that only reports failed services."""
        console.print("[yellow]🔍 Checking server health across fleet...[/yellow]")

        health_results = await self.check_fleet_health()
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
            f"[yellow]🔄 Found {len(failed_services)} services needing attention; rotation is now handled by the agent[/yellow]"
        )
        return RotationResult(
            rotated=0,
            failed=len(failed_services),
            services=[service.name for service in failed_services],
            dry_run=True if dry_run else False,
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

    def _slug_location(self, value: str) -> str:
        """Normalize a location segment to the service-name slug format."""

        return value.strip().lower().replace(" ", "-")

    def _derive_rotated_service_name(
        self, service: VPNService, target_location: str, compose_manager
    ) -> str:
        """Return the renamed service/container name for TARGET_LOCATION."""

        current_slug = self._slug_location(service.location)
        target_slug = self._slug_location(target_location)
        if not current_slug or not target_slug or current_slug == target_slug:
            return service.name
        if current_slug not in service.name:
            return service.name

        prefix, suffix = service.name.rsplit(current_slug, 1)
        candidate = f"{prefix}{target_slug}{suffix}"
        existing_names = {item.name for item in compose_manager.list_services()}
        existing_names.discard(service.name)
        if candidate not in existing_names:
            return candidate

        candidate_with_port = f"{candidate}-{service.port}"
        if candidate_with_port not in existing_names:
            return candidate_with_port
        return service.name

    async def _execute_service_rotation(self, rotation: ServiceRotation):
        """Execute server rotation for a single service"""
        # Update service configuration with new location
        compose_manager = self.fleet_manager.compose_manager

        # Get current service
        service = compose_manager.get_service(rotation.service_name)
        previous_name = service.name
        next_name = self._derive_rotated_service_name(
            service, rotation.new_location, compose_manager
        )

        # Update location and environment
        service.set_location(rotation.new_location)
        service.set_name(next_name)

        # Save updated service to compose file
        compose_manager.replace_service(previous_name, service)

        # Recreate container with new configuration
        from .docker_ops import (
            recreate_vpn_container,
            remove_container,
            start_container,
        )

        if previous_name != next_name:
            try:
                await asyncio.to_thread(remove_container, previous_name)
            except Exception:
                pass

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
