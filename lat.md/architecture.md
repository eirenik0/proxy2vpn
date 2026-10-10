# CLI Surface

The CLI accepts one active compose root per invocation and routes commands through Typer groups that share that context.

[[src/proxy2vpn/cli/main.py#main]] stores the resolved compose file on `typer.Context`, configures logging, and mounts the `profile`, `vpn`, `servers`, `system`, `fleet`, and `agent` command groups. Command modules treat the compose file as the root for all relative state instead of depending on the shell working directory.

CLI libraries imported directly at runtime are declared as direct package dependencies. In particular, Click is not left implicit through Typer, so isolated `uvx` installations contain every module needed during CLI startup.

Bulk VPN restart and restoration attempt every selected service and return a nonzero exit status if any operation fails. Successful services remain reported individually; see [[cli-regression-tests#CLI Regression Matrix#Partial Restart Failure]] and [[cli-regression-tests#CLI Regression Matrix#Failed Restoration]].

An unavailable public IP is a command failure rather than an empty successful response. Port-forward queries use `/v1/portforward` directly; automatic credential-bearing redirects remain disabled. See [[cli-regression-tests#CLI Regression Matrix#Unavailable Public IP]] and [[cli-regression-tests#CLI Regression Matrix#Canonical Control Routes]].

[[lat.md/logging#Operational Logging]] defines the shared file-log contract. Command result output remains separate from logs, including JSON commands and detached watchdog children.

# Development Verification

Local and CI verification share explicit tool contracts so dependency upgrades cannot silently expand the enforced rule set.

Ruff enforces its established error and Pyflakes baseline through an explicit rule selection in `pyproject.toml`. Tool releases may improve diagnostics, but adopting additional rule families remains a deliberate repository change instead of an implicit upgrade side effect.

`uv.lock` records the tested runtime and development dependency graph. Make targets run tools from that locked project environment, while package metadata keeps compatible major-version ranges for downstream installations.

## Release Automation

Releases follow one provenance chain from a CI-created version commit through its tag and reviewed GitHub Release to PyPI.

The `Prepare GitHub Release` workflow is the only supported entry point. It verifies the locked project, consumes news fragments, updates package and lockfile versions, creates the release commit and tag, and opens a draft GitHub Release for review.

Publishing that draft is the only PyPI trigger. The publish workflow checks that the tag belongs to the default branch and that its version matches both package metadata and the changelog; direct manual package dispatch is intentionally unavailable.

Repository tests pin these workflow invariants so later CI edits cannot quietly restore a direct package-publish or local tag-creation path.

# Compose Root Model

The compose file is the source of truth for reusable profiles, concrete VPN services, and generated support files that travel with one workspace.

[[src/proxy2vpn/adapters/compose_manager.py#ComposeManager]] owns loading, validating, backing up, and atomically saving the compose file. It preserves profile anchors under `x-vpn-base-*`, merges service-specific overrides onto those anchors, and resolves profile env files relative to the compose file so a workspace remains portable.

Service deletion tolerates absent containers, including Docker not-found errors wrapped by helpers, while preserving definitions on other Docker failures. Profile removal rejects profiles referenced by services so cleanup cannot leave dangling merges. See [[cli-regression-tests#CLI Regression Matrix#Delete Missing Containers]] and [[cli-regression-tests#CLI Regression Matrix#Referenced Profile Removal]].

The compose root also owns generated support artifacts such as the control-server auth file described in [[lat.md/agent#Watchdog Cycle]] and used by container creation paths in [[src/proxy2vpn/adapters/docker_ops.py#create_vpn_container]].

[[lat.md/security#Deployment Security]] defines authenticated control credentials, proxy publication defaults, and explicit migration for existing workspaces.

[[lat.md/gluetun-runtime#Gluetun Runtime]] consumes profiles resolved from this root and executes runtime requests. It stores no desired state and leaves compose mutation, fleet planning, and rotation with their existing owners.

# Profile And Service Models

Profiles define reusable container defaults, while VPN services add the per-container ports, location metadata, and effective environment overrides.

[[src/proxy2vpn/core/models.py#Profile]] models the profile anchor stored in compose. [[src/proxy2vpn/core/models.py#VPNService]] models the concrete service that is materialized into compose, Docker labels, and container environment variables. The service model is responsible for derived mutations such as renaming, updating location metadata, and projecting the effective state back to compose-compatible structures.

External endpoints use [[egress#External Endpoint Configuration]] instead of fabricating VPN containers or Compose definitions. The [[egress#Shared Egress Interface]] unifies common health evidence while retaining backend-specific models.

# Server Catalog

The location catalog preserves one provider-keyed cache while adapting Gluetun's upstream manifest and per-provider files.

[[src/proxy2vpn/adapters/server_manager.py#ServerManager#_download_servers]] downloads the official `gluetun-servers` manifest, fetches each declared sibling JSON file, and assembles the provider map used by validation, planning, and CLI listing commands.
