"""Fleet management for bulk VPN deployments across cities and profiles."""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rich.console import Console

from .compose_manager import ComposeManager
from .docker_ops import remove_container, stop_container
from .logging_utils import get_logger
from .models import VPNService
from .server_manager import ServerManager

console = Console()
logger = get_logger(__name__)


@dataclass
class FleetConfig:
    """Configuration for bulk VPN fleet deployment"""

    provider: str
    countries: List[str]  # ["Germany", "France", "Netherlands"]
    profiles: Dict[str, int]  # {"acc1": 2, "acc2": 8} - profile slots
    port_start: int = 20000
    naming_template: str = "{provider}-{country}-{city}"
    max_per_profile: Optional[int] = None  # Limit services per profile


@dataclass
class ServicePlan:
    """Plan for a single VPN service deployment"""

    name: str
    profile: str
    location: str
    country: str
    port: int
    provider: str


@dataclass
class DeploymentPlan:
    """Complete deployment plan for fleet"""

    provider: str
    services: List[ServicePlan] = field(default_factory=list)

    @property
    def service_names(self) -> List[str]:
        return [s.name for s in self.services]

    def add_service(
        self,
        name: str,
        profile: str,
        location: str,
        country: str,
        port: int,
        provider: str = None,
    ):
        """Add service to deployment plan"""
        self.services.append(
            ServicePlan(
                name=name,
                profile=profile,
                location=location,
                country=country,
                port=port,
                provider=provider or self.provider,
            )
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            "provider": self.provider,
            "services": [
                {
                    "name": s.name,
                    "profile": s.profile,
                    "location": s.location,
                    "country": s.country,
                    "port": s.port,
                    "provider": s.provider,
                }
                for s in self.services
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DeploymentPlan":
        """Create from dictionary"""
        plan = cls(provider=data["provider"])
        for service_data in data["services"]:
            plan.services.append(ServicePlan(**service_data))
        return plan


@dataclass
class DeploymentResult:
    """Result of fleet deployment"""

    deployed: int
    failed: int
    services: List[str]
    errors: List[str] = field(default_factory=list)


class FleetManager:
    """Manages bulk VPN deployments across cities and profiles"""

    def __init__(self, compose_file_path=None):
        from . import config

        self.server_manager = ServerManager()
        compose_path = compose_file_path or config.COMPOSE_FILE
        self.compose_manager = ComposeManager(compose_path)
        from .profile_allocator import ProfileAllocator

        self.profile_allocator = ProfileAllocator()

    def plan_deployment(self, config: FleetConfig) -> DeploymentPlan:
        """Create deployment plan for cities across countries"""
        plan = DeploymentPlan(provider=config.provider)

        # Get all available cities across countries
        all_cities = []
        for country in config.countries:
            try:
                cities = self.server_manager.list_cities(config.provider, country)
                all_cities.extend([(country, city) for city in cities])
                console.print(
                    f"[green]✓[/green] Found {len(cities)} cities in {country}"
                )
            except Exception as e:
                console.print(f"[red]❌[/red] Error getting cities for {country}: {e}")
                continue

        console.print(
            f"[blue]📍 Total: {len(all_cities)} cities across {len(config.countries)} countries[/blue]"
        )

        # Calculate total slots available
        total_slots = sum(config.profiles.values())
        if len(all_cities) > total_slots:
            console.print(
                f"[yellow]⚠ Warning: {len(all_cities)} cities but only {total_slots} profile slots[/yellow]"
            )
            console.print(f"[yellow]  Using first {total_slots} cities[/yellow]")
            all_cities = all_cities[:total_slots]

        # Setup profile allocator
        self.profile_allocator.setup_profiles(config.profiles)

        # Allocate profiles and ports
        current_port = config.port_start

        for country, city in all_cities:
            profile_slot = self.profile_allocator.get_next_available(config.profiles)
            if not profile_slot:
                console.print("[red]❌ No more profile slots available[/red]")
                break

            # Generate service name using template
            service_name = config.naming_template.format(
                provider=config.provider,
                country=country.lower().replace(" ", "-"),
                city=city.lower().replace(" ", "-"),
            )

            # Sanitize service name
            service_name = self._sanitize_service_name(service_name)

            plan.add_service(
                name=service_name,
                profile=profile_slot.name,
                location=city,
                country=country,
                port=current_port,
                provider=config.provider,
            )

            # Allocate the slot
            self.profile_allocator.allocate_slot(profile_slot.name, service_name)

            current_port += 1

        return plan

    def _validate_service_locations(
        self, services: List[ServicePlan]
    ) -> tuple[List[ServicePlan], List[str]]:
        """Validate that each service's target location exists for the provider.

        Returns tuple of (valid_services, errors).
        """
        valid_services: List[ServicePlan] = []
        errors: List[str] = []

        for svc in services:
            try:
                if self.server_manager.validate_location(svc.provider, svc.location):
                    console.print(
                        f"[green]\u2713[/green] {svc.location} available for {svc.provider}"
                    )
                    valid_services.append(svc)
                else:
                    msg = f"Invalid location {svc.location} for {svc.provider}"
                    console.print(f"[red]\u274c[/red] {msg}")
                    errors.append(msg)
            except Exception as e:
                msg = f"Error validating {svc.location} for {svc.provider}: {e}"
                console.print(f"[red]\u274c[/red] {msg}")
                errors.append(msg)

        return valid_services, errors

    async def deploy_fleet(
        self, plan: DeploymentPlan, validate_servers: bool = True, parallel: bool = True
    ) -> DeploymentResult:
        """Execute bulk deployment with server validation"""

        skipped = 0
        errors: List[str] = []

        if validate_servers:
            console.print("[yellow]🔍 Validating server availability...[/yellow]")
            valid_services, validation_errors = self._validate_service_locations(
                plan.services
            )
            skipped = len(plan.services) - len(valid_services)
            errors.extend(validation_errors)
            if skipped:
                console.print(
                    f"[yellow]⚠ Skipping {skipped} invalid service(s)[/yellow]"
                )
            plan.services = valid_services
            if not plan.services:
                return DeploymentResult(
                    deployed=0,
                    failed=skipped,
                    services=[],
                    errors=errors,
                )

        console.print(
            f"[green]🚀 Deploying {len(plan.services)} VPN services...[/green]"
        )

        deployed = 0
        added_services: List[str] = []

        try:
            # Create services in compose file
            for service_plan in plan.services:
                # Create Docker labels for service identification and metadata
                labels = {
                    "vpn.type": "vpn",
                    "vpn.port": str(service_plan.port),
                    "vpn.provider": service_plan.provider,
                    "vpn.profile": service_plan.profile,
                    "vpn.location": service_plan.location,
                }

                vpn_service = VPNService(
                    name=service_plan.name,
                    port=service_plan.port,
                    provider=service_plan.provider,
                    profile=service_plan.profile,
                    location=service_plan.location,
                    environment={
                        "VPN_SERVICE_PROVIDER": service_plan.provider,
                        "SERVER_CITIES": service_plan.location,
                    },
                    labels=labels,
                )

                self.compose_manager.add_service(vpn_service)
                added_services.append(service_plan.name)
                console.print(f"[green]✓[/green] Created service: {service_plan.name}")

            # Start containers
            if parallel:
                await self._start_services_parallel(added_services)
            else:
                await self._start_services_sequential(added_services)

            deployed = len(added_services)

        except Exception as e:
            error_msg = f"Deployment failed: {e}"
            console.print(f"[red]❌[/red] {error_msg}")
            errors.append(error_msg)

            # Rollback any services that were added before failure
            for service_name in added_services:
                try:
                    self.compose_manager.remove_service(service_name)
                    console.print(
                        f"[yellow]↩ Rolled back service: {service_name}[/yellow]"
                    )
                except Exception as rm_err:
                    console.print(
                        f"[red]⚠ Failed to remove service {service_name}: {rm_err}"
                    )
                try:
                    await asyncio.to_thread(stop_container, service_name)
                    await asyncio.to_thread(remove_container, service_name)
                    console.print(
                        f"[yellow]🛑 Stopped and removed container: {service_name}[/yellow]"
                    )
                except Exception as cleanup_err:
                    console.print(
                        f"[red]⚠ Failed to cleanup container {service_name}: {cleanup_err}"
                    )

            return DeploymentResult(
                deployed=0,
                failed=len(plan.services) + skipped,
                services=[],
                errors=errors,
            )

        failed = len(plan.services) - deployed + skipped
        return DeploymentResult(
            deployed=deployed, failed=failed, services=plan.service_names, errors=errors
        )

    async def _start_services_parallel(self, service_names: List[str]):
        """Start services in parallel with limited concurrency"""
        from .docker_ops import recreate_vpn_container, start_container

        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent starts

        async def start_service(service_name: str):
            async with semaphore:
                try:
                    console.print(f"[blue]🔄[/blue] Starting {service_name}...")

                    # Get service and profile
                    service = self.compose_manager.get_service(service_name)
                    profile = self.compose_manager.get_profile(service.profile)

                    # Create and start container
                    container = await asyncio.to_thread(
                        recreate_vpn_container, service, profile
                    )
                    await asyncio.to_thread(start_container, container)

                    console.print(f"[green]✅[/green] Started {service_name}")

                except Exception as e:
                    console.print(f"[red]❌[/red] Failed to start {service_name}: {e}")
                    raise

        # Start all services concurrently
        tasks = [start_service(name) for name in service_names]
        await asyncio.gather(*tasks)

    async def _start_services_sequential(self, service_names: List[str]):
        """Start services one by one"""
        from .docker_ops import recreate_vpn_container, start_container

        for service_name in service_names:
            try:
                console.print(f"[blue]🔄[/blue] Starting {service_name}...")

                # Get service and profile
                service = self.compose_manager.get_service(service_name)
                profile = self.compose_manager.get_profile(service.profile)

                # Create and start container
                container = await asyncio.to_thread(
                    recreate_vpn_container, service, profile
                )
                await asyncio.to_thread(start_container, container)

                console.print(f"[green]✅[/green] Started {service_name}")

            except Exception as e:
                console.print(f"[red]❌[/red] Failed to start {service_name}: {e}")
                raise

    def _sanitize_service_name(self, name: str) -> str:
        """Sanitize service name to be Docker-compatible"""
        import re

        # Replace invalid characters with dash and remove multiple dashes
        sanitized = re.sub(r"[^A-Za-z0-9_-]", "-", name)
        sanitized = re.sub(r"-+", "-", sanitized)
        sanitized = sanitized.strip("-")
        return sanitized.lower()

    def _rebuild_profile_allocator(self) -> None:
        """Reconstruct allocator state from compose services."""
        services = self.compose_manager.list_services()
        profile_counts: Dict[str, int] = {}

        for svc in services:
            if svc.profile:
                profile_counts[svc.profile] = profile_counts.get(svc.profile, 0) + 1

        self.profile_allocator.setup_profiles(profile_counts)

        for svc in services:
            if svc.profile:
                # allocate_slot updates used_slots and tracked services
                self.profile_allocator.allocate_slot(svc.profile, svc.name)

    def _extract_country(self, service: VPNService) -> str:
        """Best-effort extraction of country from service metadata."""
        # Prefer explicit label if available
        country = (
            service.labels.get("vpn.country") if hasattr(service, "labels") else None
        )
        if country:
            return country

        provider = (
            service.provider.replace(" ", "-").lower() if service.provider else ""
        )
        city = service.location.replace(" ", "-").lower() if service.location else ""
        name = service.name.lower()

        if provider and name.startswith(provider + "-"):
            name = name[len(provider) + 1 :]

        if city and name.endswith("-" + city):
            name = name[: -(len(city) + 1)]

        return name.replace("-", " ") or "unknown"

    def get_fleet_status(self) -> Dict:
        """Get current fleet status and allocation"""
        self._rebuild_profile_allocator()

        services = self.compose_manager.list_services()
        allocation_status = self.profile_allocator.get_allocation_status()

        fleet_services: Dict[str, List[VPNService]] = {}
        country_counts: Dict[str, int] = {}
        profile_counts: Dict[str, int] = {
            name: data["used_slots"] for name, data in allocation_status.items()
        }

        for service in services:
            if service.provider:
                fleet_services.setdefault(service.provider, []).append(service)

            country = self._extract_country(service)
            country_counts[country] = country_counts.get(country, 0) + 1

        return {
            "total_services": len(services),
            "services_by_provider": fleet_services,
            "profile_allocation": allocation_status,
            "country_counts": country_counts,
            "profile_counts": profile_counts,
        }
