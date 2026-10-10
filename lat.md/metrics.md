# Watchdog Metrics

Producer-owned metrics keep cycle success, completed endpoint observations and cumulative request outcomes separate from progress timestamps and bounded action history.

## Persisted Evidence

Cycle attempts are committed before work; success advances only after a complete cycle. Endpoint completion is committed as probes return, so cancellation preserves partial evidence.

[[src/proxy2vpn/agent/metrics_models.py#AgentMetrics]] stores counters independently of action history. Monitoring reset preserves cumulative counters, increments a reset counter and clears observation freshness. Pending cycles become interrupted on the next cycle or reset. Request outcomes count committed audit records; later health rechecks do not change them.

## Read Only Collection

The independent exporter validates atomic state and history snapshots without constructing a writable store, creating keys, repairing permissions, replaying journals, probing endpoints or invoking recovery.

[[src/proxy2vpn/agent/metrics.py#collect_metrics]] exposes missing/uninitialized evidence and collection failure explicitly. Unknown health fields have known flags and omitted values. Freshness requires a recent successful completed cycle and recent completed observation; scrapes never change timestamps. Exporter disappearance is detected by Prometheus up, and watchdog death by freshness expiry.

## Deployment And Labels

The CLI exporter binds to localhost on port 9109 by default. Labels contain domain-separated HMAC deployment/endpoint IDs and fixed source, action, result and cycle outcome vocabularies.

Private IPv4 binds are explicit opt-ins; remote deployments need firewall restrictions and an authenticated TLS reverse proxy. Counters reset only when storage is replaced or deleted; identity-key replacement changes series IDs. Collection rejects inventories over 1000 endpoints. No raw paths, service names, credentials, exit IPs, errors or incident IDs appear in labels. See [[metrics-tests#Metrics Tests]].
