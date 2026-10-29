---
name: proxy2vpn-design
description: Design proxy2vpn features or refactors that change compose ownership, endpoint capabilities, runtime interfaces, fleet allocation, or persisted state.
---

# Proxy2VPN design

Ground the design in `lat.md/architecture.md` and the domain document for the affected behavior. Follow the repository's `lat search` and `lat expand` requirements first.

## Ownership to preserve

- `src/proxy2vpn/cli/main.py` owns the active compose context. Relative support files belong beside that compose file, independently of shell cwd.
- `adapters/compose_manager.py` owns desired Compose state, profile anchors, backups, and atomic saves. `core/models.py` owns profile/service values and derived mutations.
- `core/egress.py` owns endpoint identity, capabilities, and observations. An endpoint id is distinct from its observed exit IP. Missing evidence stays unknown.
- `adapters/gluetun_runtime.py` owns Gluetun runtime requests and observations, without owning desired Compose state. `adapters/fleet_manager.py` owns fleet execution and rotation.
- `agent/recovery_policy.py` makes pure decisions from supplied observations, history, settings, and time. `agent/runtime.py` performs effects and delayed rechecks.
- `agent/state.py` and `core/private_storage.py` own persistence and concurrency. Inspect their transaction and reset contracts before changing writers.

Paths above are relative to `src/proxy2vpn/` unless already prefixed. Read `lat.md/egress.md`, `lat.md/gluetun-runtime.md`, `lat.md/fleet.md`, or `lat.md/recovery-policy.md` as appropriate.

## Design the change

When available, use pstack's `how` for current behavior, `why` for historical constraints, and `architect` for the proposed contract. Trace actual callers before choosing ownership. These project instructions also work without the plugin.

Show the caller's usage, important types, owner of each state transition, dependency direction, and affected callers. Compare different ownership choices when there is a real architectural decision. Keep Docker, HTTP, environment reads, and clock reads outside recovery policy.

Check the design against the relevant existing cases: partial fleet failure, requested/final rotation names, external-only inventories, source changes, concurrent approval, and reset during delayed work. Read the matching test specifications rather than inventing a new contract.

If implementation is requested, migrate callers with the interface change. Update the affected lat.md concepts and test specs, then use `$proxy2vpn-verify` for validation. If only design is requested, return the design without implementation.
