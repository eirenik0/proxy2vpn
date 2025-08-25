"""Profile allocation system for managing account slots across VPN services."""

from dataclasses import dataclass, field

from .display_utils import console
from .logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class ProfileSlot:
    """Profile allocation tracking"""

    name: str
    total_slots: int
    used_slots: int = 0
    services: list[str] = field(default_factory=list)

    @property
    def available_slots(self) -> int:
        return self.total_slots - self.used_slots

    @property
    def utilization_ratio(self) -> float:
        """Get utilization ratio (0.0 to 1.0)"""
        if self.total_slots == 0:
            return 1.0
        return self.used_slots / self.total_slots

    @property
    def utilization_percent(self) -> str:
        """Get utilization as percentage string"""
        return f"{self.utilization_ratio * 100:.1f}%"


class ProfileAllocator:
    """Manages automatic profile allocation with slot tracking"""

    def __init__(self):
        self.slots: dict[str, ProfileSlot] = {}
        self._last_allocated: str | None = None

    def setup_profiles(self, profile_config: dict[str, int]):
        """Initialize profile slots from config"""
        self.slots = {
            name: ProfileSlot(name=name, total_slots=slots)
            for name, slots in profile_config.items()
        }
        logger.info(
            f"Setup {len(self.slots)} profiles with total {sum(profile_config.values())} slots"
        )
        console.print(
            f"[green]📋 Setup {len(self.slots)} profiles with {sum(profile_config.values())} total slots[/green]"
        )

    def get_next_available(
        self, profile_config: dict[str, int] | None = None
    ) -> ProfileSlot | None:
        """Get next available profile slot using round-robin with load balancing"""
        if profile_config and not self.slots:
            self.setup_profiles(profile_config)

        # Find profiles with available slots
        available_profiles = [
            slot for slot in self.slots.values() if slot.available_slots > 0
        ]

        if not available_profiles:
            logger.warning("No profile slots available")
            console.print("[yellow]⚠️ No profile slots available[/yellow]")
            return None

        # Round-robin with load balancing: choose profile with lowest utilization
        # This ensures even distribution across profiles
        best_profile = min(available_profiles, key=lambda p: p.utilization_ratio)

        logger.debug(
            f"Selected profile {best_profile.name} "
            f"({best_profile.used_slots}/{best_profile.total_slots})"
        )

        self._last_allocated = best_profile.name
        return best_profile

    def allocate_slot(self, profile_name: str, service_name: str) -> bool:
        """Allocate a slot to a service"""
        if profile_name not in self.slots:
            logger.error(f"Profile {profile_name} not found")
            return False

        slot = self.slots[profile_name]
        if slot.available_slots <= 0:
            logger.error(f"No available slots in profile {profile_name}")
            return False

        if service_name in slot.services:
            logger.warning(
                f"Service {service_name} already allocated to {profile_name}"
            )
            console.print(
                f"[yellow]⚠️ Service already allocated:[/yellow] {service_name} → {profile_name}"
            )
            return False

        slot.used_slots += 1
        slot.services.append(service_name)

        logger.info(
            f"Allocated slot to {service_name} in profile {profile_name} "
            f"({slot.used_slots}/{slot.total_slots})"
        )
        console.print(
            f"[green]✅ Allocated slot:[/green] {service_name} → {profile_name} ({slot.used_slots}/{slot.total_slots})"
        )
        return True

    def release_slot(self, service_name: str) -> bool:
        """Release slot when service is deleted"""
        for profile_name, slot in self.slots.items():
            if service_name in slot.services:
                slot.services.remove(service_name)
                slot.used_slots -= 1

                logger.info(
                    f"Released slot for {service_name} from profile {profile_name} "
                    f"({slot.used_slots}/{slot.total_slots})"
                )
                console.print(
                    f"[blue]♾️ Released slot:[/blue] {service_name} from {profile_name} ({slot.used_slots}/{slot.total_slots})"
                )
                return True

        logger.warning(f"Service {service_name} not found in any profile slot")
        return False

    def get_allocation_status(self) -> dict[str, dict]:
        """Get current allocation status for all profiles"""
        return {
            name: {
                "total_slots": slot.total_slots,
                "used_slots": slot.used_slots,
                "available_slots": slot.available_slots,
                "services": slot.services.copy(),
                "utilization": slot.utilization_percent,
            }
            for name, slot in self.slots.items()
        }

    def get_profile_for_service(self, service_name: str) -> str | None:
        """Get profile name that service is allocated to"""
        for profile_name, slot in self.slots.items():
            if service_name in slot.services:
                return profile_name
        return None

    def validate_allocation(self) -> list[str]:
        """Validate current allocation state and return issues"""
        issues = []

        for profile_name, slot in self.slots.items():
            # Check for over-allocation
            if slot.used_slots > slot.total_slots:
                issues.append(
                    f"Profile {profile_name} over-allocated: "
                    f"{slot.used_slots}/{slot.total_slots}"
                )

            # Check for negative slots
            if slot.used_slots < 0:
                issues.append(
                    f"Profile {profile_name} has negative used_slots: {slot.used_slots}"
                )

            # Check services list consistency
            if len(slot.services) != slot.used_slots:
                issues.append(
                    f"Profile {profile_name} services count mismatch: "
                    f"{len(slot.services)} services but {slot.used_slots} used_slots"
                )

        return issues

    def rebalance_profiles(self) -> dict[str, list[str]]:
        """Suggest rebalancing of services across profiles"""
        suggestions = {}

        if not self.slots:
            return suggestions

        # Calculate average utilization
        total_services = sum(slot.used_slots for slot in self.slots.values())
        total_capacity = sum(slot.total_slots for slot in self.slots.values())

        if total_capacity == 0:
            return suggestions

        target_ratio = total_services / total_capacity

        # Find over-utilized and under-utilized profiles
        over_utilized = []
        under_utilized = []

        for slot in self.slots.values():
            if slot.utilization_ratio > target_ratio + 0.1:  # 10% threshold
                over_utilized.append(slot)
            elif (
                slot.utilization_ratio < target_ratio - 0.1 and slot.available_slots > 0
            ):
                under_utilized.append(slot)

        # Generate suggestions
        for over_slot in over_utilized:
            excess_services = int(
                over_slot.used_slots - (target_ratio * over_slot.total_slots)
            )
            if excess_services > 0 and over_slot.services:
                suggestions[f"Move from {over_slot.name}"] = over_slot.services[
                    -excess_services:
                ]

        for under_slot in under_utilized:
            available = under_slot.available_slots
            if available > 0:
                suggestions[f"Move to {under_slot.name}"] = [
                    f"Can accept {available} services"
                ]

        return suggestions

    def get_summary(self) -> dict:
        """Get summary statistics"""
        if not self.slots:
            return {
                "total_profiles": 0,
                "total_slots": 0,
                "used_slots": 0,
                "available_slots": 0,
            }

        total_slots = sum(slot.total_slots for slot in self.slots.values())
        used_slots = sum(slot.used_slots for slot in self.slots.values())
        available_slots = total_slots - used_slots

        return {
            "total_profiles": len(self.slots),
            "total_slots": total_slots,
            "used_slots": used_slots,
            "available_slots": available_slots,
            "overall_utilization": f"{(used_slots / total_slots * 100):.1f}%"
            if total_slots > 0
            else "0.0%",
        }
