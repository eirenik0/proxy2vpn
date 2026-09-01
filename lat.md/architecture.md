# CLI Surface

The CLI accepts one active compose root per invocation and routes commands through Typer groups that share that context.

[[src/proxy2vpn/cli/main.py#main]] stores the resolved compose file on `typer.Context`, configures logging, and mounts the `profile`, `vpn`, `servers`, `system`, `fleet`, and `agent` command groups. Command modules treat the compose file as the root for all relative state instead of depending on the shell working directory.

CLI libraries imported directly at runtime are declared as direct package dependencies. In particular, Click is not left implicit through Typer, so isolated `uvx` installations contain every module needed during CLI startup.

# Compose Root Model

The compose file is the source of truth for reusable profiles, concrete VPN services, and generated support files that travel with one workspace.

[[src/proxy2vpn/adapters/compose_manager.py#ComposeManager]] owns loading, validating, backing up, and atomically saving the compose file. It preserves profile anchors under `x-vpn-base-*`, merges service-specific overrides onto those anchors, and resolves profile env files relative to the compose file so a workspace remains portable.

The compose root also owns generated support artifacts such as the control-server auth file described in [[lat.md/agent#Watchdog Cycle]] and used by container creation paths in [[src/proxy2vpn/adapters/docker_ops.py#create_vpn_container]].

# Profile And Service Models

Profiles define reusable container defaults, while VPN services add the per-container ports, location metadata, and effective environment overrides.

[[src/proxy2vpn/core/models.py#Profile]] models the profile anchor stored in compose. [[src/proxy2vpn/core/models.py#VPNService]] models the concrete service that is materialized into compose, Docker labels, and container environment variables. The service model is responsible for derived mutations such as renaming, updating location metadata, and projecting the effective state back to compose-compatible structures.

# Server Catalog

The location catalog preserves one provider-keyed cache while adapting Gluetun's upstream manifest and per-provider files.

[[src/proxy2vpn/adapters/server_manager.py#ServerManager#_download_servers]] downloads the official `gluetun-servers` manifest, fetches each declared sibling JSON file, and assembles the provider map used by validation, planning, and CLI listing commands.
