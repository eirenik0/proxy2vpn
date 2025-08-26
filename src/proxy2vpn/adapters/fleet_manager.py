"""Fleet management for bulk VPN deployments across cities and profiles."""

import asyncio
from dataclasses import dataclass, field

from .compose_manager import ComposeManager
from .display_utils import console
from .docker_ops import ensure_network, remove_container, stop_container
from .logging_utils import get_logger
from proxy2vpn.core.models import VPNService
from .server_manager import ServerManager

logger = get_logger(__name__)


@dataclass
class FleetConfig:
    """Configuration for bulk VPN fleet deployment"""

    provider: str
    countries: list[str]  # ["Germany", "France", "Netherlands"]
    profiles: dict[str, int]  # {"acc1": 2, "acc2": 8} - profile slots
    port_start: int = 20000
    control_port_start: int = 30000
    naming_template: str = "{provider}-{country}-{city}"
    max_per_profile: int | None = None  # Limit services per profile
    unique_ips: bool = False  # Ensure unique city/IP combinations


@dataclass
class ServicePlan:
    """Plan for a single VPN service deployment"""

    name: str
    profile: str
    location: str
    country: str
    port: int
    control_port: int
    provider: str
    hostname: str | None = None
    ip: str | None = None


@dataclass
class DeploymentPlan:
    """Complete deployment plan for fleet"""

    provider: str
    services: list[ServicePlan] = field(default_factory=list)

    @property
    def service_names(self) -> list[str]:
        return [s.name for s in self.services]

    def add_service(
        self,
        name: str,
        profile: str,
        location: str,
        country: str,
        port: int,
        control_port: int,
        provider: str,
        hostname: str | None = None,
        ip: str | None = None,
    ):
        """Add service to deployment plan"""
        self.services.append(
            ServicePlan(
                name=name,
                profile=profile,
                location=location,
                country=country,
                port=port,
                control_port=control_port,
                provider=provider or self.provider,
                hostname=hostname,
                ip=ip,
            )
        )

    def to_dict(self) -> dict:
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
                    "hostname": s.hostname,
                    "ip": s.ip,
                    "control_port": s.control_port,
                }
                for s in self.services
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeploymentPlan":
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
    services: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class FleetManager:
    """Manages bulk VPN deployments across cities and profiles"""

    def __init__(self, compose_file_path=None):
        from proxy2vpn.core import config

        self.server_manager = ServerManager()
        compose_path = compose_file_path or config.COMPOSE_FILE
        self.compose_manager = ComposeManager(compose_path)
        from .profile_allocator import ProfileAllocator

        self.profile_allocator = ProfileAllocator()

    def plan_deployment(self, config: FleetConfig) -> DeploymentPlan:
        """Create deployment plan for cities across countries"""
        plan = DeploymentPlan(provider=config.provider)

        defined_profiles = {p.name for p in self.compose_manager.list_profiles()}
        missing_profiles = set(config.profiles) - defined_profiles
        if missing_profiles:
            raise ValueError(f"Unknown profiles: {', '.join(sorted(missing_profiles))}")

        if config.unique_ips:
            data = self.server_manager.data or self.server_manager.update_servers()
            prov = data.get(config.provider, {})
            servers = prov.get("servers", [])
            all_entries: list[tuple[str, str, str, str]] = []
            used_ips: set[str] = set()
            used_cities: set[str] = set()
            for srv in servers:
                country = srv.get("country")
                city = srv.get("city")
                if country not in config.countries or not city:
                    continue
                ips = srv.get("ips") or []
                ip = next((ip for ip in ips if "." in ip), None)
                if not ip or ip in used_ips or city in used_cities:
                    continue
                hostname = srv.get("hostname", "")
                used_ips.add(ip)
                used_cities.add(city)
                all_entries.append((country, city, hostname, ip))

            console.print(
                f"[blue]📍 Total: {len(all_entries)} unique city/IP pairs across {len(config.countries)} countries[/blue]"
            )

            total_slots = sum(config.profiles.values())
            if len(all_entries) > total_slots:
                console.print(
                    f"[yellow]⚠ Warning: {len(all_entries)} city/IP pairs but only {total_slots} profile slots[/yellow]"
                )
                console.print(f"[yellow]  Using first {total_slots} entries[/yellow]")
                all_entries = all_entries[:total_slots]

            self.profile_allocator.setup_profiles(config.profiles)
            current_port = config.port_start
            current_control_port = config.control_port_start

            for country, city, hostname, ip in all_entries:
                profile_slot = self.profile_allocator.get_next_available(
                    config.profiles
                )
                if not profile_slot:
                    console.print("[red]❌ No more profile slots available[/red]")
                    break

                service_name = config.naming_template.format(
                    provider=config.provider,
                    country=country.lower().replace(" ", "-"),
                    city=city.lower().replace(" ", "-"),
                )
                service_name = self._sanitize_service_name(service_name)

                plan.add_service(
                    name=service_name,
                    profile=profile_slot.name,
                    location=city,
                    country=country,
                    port=current_port,
                    control_port=current_control_port,
                    provider=config.provider,
                    hostname=hostname,
                    ip=ip,
                )

                self.profile_allocator.allocate_slot(profile_slot.name, service_name)
                current_port += 1
                current_control_port += 1

            return plan

        # Existing behaviour without unique IPs
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

        total_slots = sum(config.profiles.values())
        if len(all_cities) > total_slots:
            console.print(
                f"[yellow]⚠ Warning: {len(all_cities)} cities but only {total_slots} profile slots[/yellow]"
            )
            console.print(f"[yellow]  Using first {total_slots} cities[/yellow]")
            all_cities = all_cities[:total_slots]

        self.profile_allocator.setup_profiles(config.profiles)

        current_port = config.port_start
        current_control_port = config.control_port_start

        for country, city in all_cities:
            profile_slot = self.profile_allocator.get_next_available(config.profiles)
            if not profile_slot:
                console.print("[red]❌ No more profile slots available[/red]")
                break

            service_name = config.naming_template.format(
                provider=config.provider,
                country=country.lower().replace(" ", "-"),
                city=city.lower().replace(" ", "-"),
            )
            service_name = self._sanitize_service_name(service_name)

            plan.add_service(
                name=service_name,
                profile=profile_slot.name,
                location=city,
                country=country,
                port=current_port,
                control_port=current_control_port,
                provider=config.provider,
            )

            self.profile_allocator.allocate_slot(profile_slot.name, service_name)
            current_port += 1
            current_control_port += 1

        return plan

    def _validate_service_locations(
        self, services: list[ServicePlan]
    ) -> tuple[list[ServicePlan], list[str]]:
        """Validate that each service's target location exists for the provider.

        Returns tuple of (valid_services, errors).
        """
        valid_services: list[ServicePlan] = []
        errors: list[str] = []

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

    def _handle_server_validation(
        self, plan: DeploymentPlan, validate_servers: bool
    ) -> tuple[list[ServicePlan], int, list[str]]:
        """Handle server validation and return filtered services, skipped count, and errors."""
        if not validate_servers:
            return plan.services, 0, []

        console.print("[yellow]🔍 Validating server availability...[/yellow]")
        valid_services, validation_errors = self._validate_service_locations(
            plan.services
        )
        skipped = len(plan.services) - len(valid_services)

        if skipped:
            console.print(f"[yellow]⚠ Skipping {skipped} invalid service(s)[/yellow]")

        return valid_services, skipped, validation_errors

    def _create_service_from_plan(self, service_plan: ServicePlan) -> VPNService:
        """Create a VPNService object from a ServicePlan."""
        labels = {
            "vpn.type": "vpn",
            "vpn.port": str(service_plan.port),
            "vpn.control_port": str(service_plan.control_port),
            "vpn.provider": service_plan.provider,
            "vpn.profile": service_plan.profile,
            "vpn.location": service_plan.location,
        }
        if service_plan.hostname:
            labels["vpn.hostname"] = service_plan.hostname

        env = {
            "VPN_SERVICE_PROVIDER": service_plan.provider,
            "SERVER_COUNTRIES": service_plan.country,
        }
        if service_plan.hostname:
            env["SERVER_HOSTNAMES"] = service_plan.hostname
        else:
            env["SERVER_CITIES"] = service_plan.location

        return VPNService.create(
            name=service_plan.name,
            port=service_plan.port,
            control_port=service_plan.control_port,
            provider=service_plan.provider,
            profile=service_plan.profile,
            location=service_plan.location,
            environment=env,
            labels=labels,
        )

    def _add_service_with_force_handling(
        self, vpn_service: VPNService, force: bool
    ) -> None:
        """Add service to compose manager, handling existing services with force flag."""
        try:
            self.compose_manager.add_service(vpn_service)
        except ValueError as e:
            if "already exists" in str(e) and force:
                self.compose_manager.remove_service(vpn_service.name)
                self.compose_manager.add_service(vpn_service)
            else:
                raise

    async def _create_service_definitions(
        self, services: list[ServicePlan], force: bool, added_services: list[str]
    ) -> None:
        """Create service definitions in compose file and update added_services list."""
        await asyncio.to_thread(ensure_network, force)

        for service_plan in services:
            vpn_service = self._create_service_from_plan(service_plan)
            self._add_service_with_force_handling(vpn_service, force)
            added_services.append(service_plan.name)
            console.print(f"[green]✓[/green] Created service: {service_plan.name}")

    async def _deploy_containers(
        self, added_services: list[str], parallel: bool, force: bool
    ) -> None:
        """Deploy containers either in parallel or sequential mode."""
        if parallel:
            await self._start_services_parallel(added_services, force)
        else:
            await self._start_services_sequential(added_services, force)

    async def _handle_deployment_failure(
        self, added_services: list[str], error: Exception
    ) -> str:
        """Handle deployment failure by rolling back added services."""
        error_msg = f"Deployment failed: {error}"
        console.print(f"[red]❌[/red] {error_msg}")

        for service_name in added_services:
            try:
                self.compose_manager.remove_service(service_name)
                console.print(f"[yellow]↩ Rolled back service: {service_name}[/yellow]")
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

        return error_msg

    async def deploy_fleet(
        self,
        plan: DeploymentPlan,
        validate_servers: bool = True,
        parallel: bool = True,
        force: bool = False,
    ) -> DeploymentResult:
        """Execute bulk deployment with server validation"""
        # Handle server validation
        valid_services, skipped, errors = self._handle_server_validation(
            plan, validate_servers
        )

        if not valid_services:
            return DeploymentResult(
                deployed=0,
                failed=skipped,
                errors=errors,
            )

        console.print(
            f"[green]🚀 Deploying {len(valid_services)} VPN services...[/green]"
        )

        added_services: list[str] = []
        deployed = 0

        try:
            # Create service definitions
            await self._create_service_definitions(
                valid_services, force, added_services
            )

            # Deploy containers
            await self._deploy_containers(added_services, parallel, force)

            deployed = len(added_services)

        except Exception as e:
            error_msg = await self._handle_deployment_failure(added_services, e)
            errors.append(error_msg)
            return DeploymentResult(
                deployed=0,
                failed=len(valid_services) + skipped,
                services=[],
                errors=errors,
            )

        failed = len(valid_services) - deployed + skipped
        return DeploymentResult(
            deployed=deployed,
            failed=failed,
            services=[s.name for s in valid_services],
            errors=errors,
        )

    async def _start_services_parallel(self, service_names: list[str], force: bool):
        """Start services in parallel with limited concurrency"""
        from .docker_ops import start_vpn_service

        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent starts

        async def start_service(service_name: str):
            async with semaphore:
                try:
                    console.print(f"[blue]🔄[/blue] Starting {service_name}...")

                    # Get service and profile
                    service = self.compose_manager.get_service(service_name)
                    profile = self.compose_manager.get_profile(service.profile)

                    await asyncio.to_thread(start_vpn_service, service, profile, force)

                    console.print(f"[green]✅[/green] Started {service_name}")

                except Exception as e:
                    console.print(f"[red]❌[/red] Failed to start {service_name}: {e}")
                    raise

        # Start all services concurrently
        tasks = [start_service(name) for name in service_names]
        await asyncio.gather(*tasks)

    async def _start_services_sequential(self, service_names: list[str], force: bool):
        """Start services one by one"""
        from .docker_ops import start_vpn_service

        for service_name in service_names:
            try:
                console.print(f"[blue]🔄[/blue] Starting {service_name}...")

                # Get service and profile
                service = self.compose_manager.get_service(service_name)
                profile = self.compose_manager.get_profile(service.profile)

                await asyncio.to_thread(start_vpn_service, service, profile, force)

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
        profile_counts: dict[str, int] = {}

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

    def get_fleet_status(self) -> dict:
        """Get current fleet status and allocation"""
        self._rebuild_profile_allocator()

        services = self.compose_manager.list_services()
        allocation_status = self.profile_allocator.get_allocation_status()

        fleet_services: dict[str, list[VPNService]] = {}
        country_counts: dict[str, int] = {}
        profile_counts: dict[str, int] = {
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
