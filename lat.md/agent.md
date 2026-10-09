# Watchdog Cycle

The watchdog runs one remediation policy loop per compose root and persists progress often enough for status commands to observe live work.

[[src/proxy2vpn/agent/runtime.py#AgentWatchdog#run_cycle]] loads the compose-root state, removes orphaned containers, batch-assesses services, updates per-service snapshots, and then applies bounded remediation such as tunnel restart, service restore, or fleet rotation. The cycle uses the shared health-assessment layer from [[lat.md/health#Health Assessment]] rather than duplicating probe logic.

Cycle, service, action, and incident events follow [[lat.md/logging#Operational Logging]]. Each cycle has a unique ID, concurrent checks isolate service/provider context, and incident events bind their IDs without contaminating later services.

Runtime inspection, incident evidence, control requests, restoration, and orphan cleanup use [[lat.md/gluetun-runtime#Gluetun Runtime]]. The watchdog retains recovery execution, rechecks, timing, and action history; deterministic decisions use [[recovery-policy#Recovery Policy]]; its shared assessor receives the same injected runtime instance.

[[egress#Watchdog And Fleet Workflows]] adds external endpoint inventory and investigation-only incidents. External-only roots need no Docker or Compose file. Capabilities block unsupported recovery before execution, while existing Gluetun actions and state remain compatible.

# Incidents And State

Agent state is stored next to the compose file so watchdog status, daemon metadata, and incident history move with the workspace.

[[src/proxy2vpn/agent/state.py#AgentStateStore]] persists the current watchdog status, service snapshots, and append-only incident records under the compose root. This keeps `agent status`, daemon supervision, and post-incident investigations aligned with the exact compose file the watchdog was monitoring.
