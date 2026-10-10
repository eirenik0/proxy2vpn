# Deployment Planning

Fleet planning converts requested countries and profile capacities into a concrete deployment plan with unique names, proxy ports, and control ports.

[[src/proxy2vpn/adapters/fleet_manager.py#FleetManager]] groups profiles by provider, validates profile availability, allocates the next free ports from the compose root, and emits a [[src/proxy2vpn/adapters/fleet_manager.py#DeploymentPlan]] made of concrete service records. Planning is additive: it treats the existing compose file as occupied state and only picks unused names and ports.

# Profile Allocation

Profile allocation spreads services across profile slot budgets so multi-account fleets can consume capacity predictably.

[[src/proxy2vpn/adapters/profile_allocator.py#ProfileAllocator]] tracks per-profile slot counts and chooses the next profile with remaining capacity using a lowest-utilization heuristic. This keeps allocations balanced without needing one static profile-per-country mapping.

# Structured Fleet Status

Fleet status keeps JSON and YAML machine-readable for managed and external endpoints, including allocation and optional health evidence without tables or probe progress on stdout.

[[src/proxy2vpn/adapters/fleet_commands.py#fleet_status]] includes normalized health assessments under `health` when requested. Human-readable allocation and diagnostic displays remain limited to table output. Regression coverage is specified in [[cli-regression-tests#CLI Regression Matrix#Structured Fleet Health]].

# Rotation Execution

Rotation updates compose state first, then recreates the affected container so the canonical service definition remains the source of truth.

[[src/proxy2vpn/adapters/server_monitor.py#ServerMonitor#_generate_rotation_plan]] finds replacement cities within the same country while excluding recently failed candidates for the same provider. [[src/proxy2vpn/adapters/server_monitor.py#ServerMonitor#_execute_service_rotation]] then mutates the service location, optionally renames the service to reflect the new city slug, persists the updated compose entry, recreates the container, and verifies health before accepting the rotation.
