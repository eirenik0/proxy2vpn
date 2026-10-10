# Proxy2VPN

Proxy2VPN turns Docker-managed Gluetun containers into a compose-root VPN fleet with shared profiles, health analysis, and automated remediation.

## Documentation Map

This index points to the stable concepts that explain how the CLI, compose state, fleet planner, diagnostics, and watchdog fit together.

- [[architecture]]: CLI context, compose-root ownership, and the profile/service domain model.
- [[health]]: Diagnostics, normalized health assessment, and provider-scoped rotation memory.
- [[fleet]]: Deployment planning, slot allocation, and rotation execution.
- [[agent]]: Watchdog cycle behavior and compose-root incident state.
- [[logging]]: Shared event schema, secret redaction, and task-local operational context.
- [[logging-tests]]: Regression specifications for logging compatibility, redaction, and CLI output.
- [[gluetun-runtime]]: Gluetun runtime operations, dependency injection, and observed-state results.
- [[gluetun-runtime-tests]]: Runtime-interface and orchestration regression specifications.

- [[recovery-policy]]: Pure recovery decisions, observation requirements, limits, and orchestration ownership.
- [[recovery-policy-tests]]: Deterministic policy and interrupted-execution specifications.

- [[egress]]: Shared endpoint identity, capabilities, external configuration, and source-specific health.
- [[egress-tests]]: Controlled CONNECT/TLS, unsupported-action, and workflow regression specifications.
- [[security]]: Authenticated controls, proxy publication defaults, and explicit deployment migration.
- [[security-tests]]: Credential isolation, authenticated requests, and binding compatibility specifications.

- [[agent-evidence-tests]]: Secrecy contract regression specifications for state, LLM requests and migration.

- [[agent-storage-tests]]: Private storage, concurrency, reset fencing, and interruption specifications.

- [[live-security-tests]]: Pinned live Gluetun authentication, publication and migration release validation.

- [[incident-retention-tests]]: Retention boundaries, preview purity, concurrent compaction and interruption specifications.

- [[metrics]]: Persisted counters, observation freshness and read-only Prometheus collection.
- [[metrics-tests]]: Counter durability, unknown evidence, collection purity and reset fencing.
- [[monitoring]]: Dashboards, held alert evidence, isolated monitoring deployment and runbooks.
- [[monitoring-tests]]: Operator contract and current blocked-recovery gauge specifications.
- [[iproyal-mobile]]: First dedicated-mobile provider discovery, uncertainty and scoped dispatch/storage design.
- [[iproyal-discovery-tests]]: Reproducible local control/CONNECT contract discovery.
