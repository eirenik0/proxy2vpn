# Gluetun Runtime

The Gluetun runtime module centralizes Docker and control-client operations behind data results, so health assessment and watchdog policy can be tested independently of live containers.

[[src/proxy2vpn/adapters/gluetun_runtime.py#GluetunRuntime]] is the existing Gluetun implementation, with [[src/proxy2vpn/adapters/gluetun_runtime.py#GluetunRuntimeInterface]] defining the seam for orchestration and test doubles. It introduces no plugin registry, alternate provider framework, or desired-state cache.

## Interface And Results

Six operations provide runtime health inputs, incident evidence, control reachability, tunnel restart, service recreation, and compose-scoped orphan cleanup without exposing Docker objects or HTTP clients.

`inspect(service, lines, timeout)` returns [[src/proxy2vpn/adapters/gluetun_runtime.py#RuntimeInspection]] with container status, diagnostic results, control reachability, direct IP, egress IP, and errors keyed by operation. Missing and stopped containers skip runtime probes. Inspection or diagnostic failure makes the result incomplete; health assessment retains the existing failed-assessment behavior. Optional direct-IP, control, refresh, and egress failures preserve other available evidence.

Runtime lookup uses the strict option of [[src/proxy2vpn/adapters/docker_ops.py#get_container_by_service_name]] so Docker enumeration failures produce an incomplete observation and `assessment_failed`, rather than masquerading as `container_missing`. Existing CLI/helper callers retain tolerant lookup by default.

`collect_evidence(service_name, lines, timeout)` returns [[src/proxy2vpn/adapters/gluetun_runtime.py#RuntimeEvidence]] with observed status, stripped recent log lines, diagnostic results, and independent errors. An absent live container has a `None` evidence status so investigations retain their persisted snapshot fallback. Log failure does not discard successful diagnostics, and diagnostic failure does not discard readable logs.

`control_status(service)`, `restart_tunnel(service)`, and `restore(service, profile)` return [[src/proxy2vpn/adapters/gluetun_runtime.py#RuntimeActionResult]] with `success` and optional error text. Success means the runtime request completed; it does not claim that service health has recovered. The watchdog still performs delayed rechecks and decides the persisted action outcome.

`cleanup_orphans(manager)` returns [[src/proxy2vpn/adapters/gluetun_runtime.py#RuntimeCleanupResult]] with removed names and any raised cleanup error. Existing helper tolerance for unavailable Docker enumeration and individual removal failures remains compatible. The caller supplies the active ComposeManager; cleanup retains its compose-owner label filter.

## Dependency Injection And Async Work

Orchestration injects one runtime instance, while the implementation accepts Docker helpers, a control-client factory, and a direct-IP fetcher for deterministic tests without a daemon.

[[src/proxy2vpn/agent/runtime.py#AgentWatchdog]] accepts `runtime=` and gives the same instance to [[src/proxy2vpn/core/services/health_assessment.py#HealthAssessmentService]]. Tests replace complete runtime results instead of patching SDK objects or HTTP clients in orchestration. Implementation tests inject dependencies and exercise the same methods as callers.

Synchronous container lookup/refresh, log reading, diagnostics, direct-IP fetching, recreation, and cleanup run through asyncio.to_thread. Egress probes and control requests remain async. Exceptions become explicit results, while asyncio cancellation propagates through all operations and control clients close via their async context manager. Cancelling a thread await does not forcibly stop an already-running synchronous operation, matching the existing recreation behavior.

Probe timeouts and per-call overrides are preserved. Control status and restart use the configured timeout and retry count, and the existing GluetunControlClient continues to own environment-backed authentication and restart endpoint fallback behavior.

## State And Compose Ownership

The runtime module reads observed state and executes requested operations; compose definitions, recovery policy, and rotation planning remain with their existing owners.

[[lat.md/architecture#Compose Root Model]] remains the source of desired state. Restoration receives the exact profile resolved by ComposeManager, preserving its compose-root base directory, relative env-file path, and control-auth mount. It reuses the existing forced start/recreation helper and discards the SDK return value. Existing CLI Docker helpers remain compatible.

[[lat.md/agent#Watchdog Cycle]] still owns runtime execution, remediation timing, recovery rechecks, snapshots, incidents, and actions; [[recovery-policy#Recovery Policy]] owns deterministic recovery choices and limits. [[egress#Gluetun Adapter]] preserves existing source-specific scores and classifications; [[lat.md/health#Health Assessment]] owns peer evidence and the compatible public result. Fleet planning, compose mutations, and rotation remain outside the runtime module. Profile validation continues to use the existing env-file parser; it does not perform runtime I/O.

Tests are specified in [[lat.md/gluetun-runtime-tests#Gluetun Runtime Tests]].
