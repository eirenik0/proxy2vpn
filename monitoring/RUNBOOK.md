# Proxy2VPN monitoring runbooks

Alerts describe evidence, not physical VPN sessions. Deployment and endpoint IDs are opaque; use the matching deployment's `agent status --json`, `agent incidents --all --json` and configured inventory to investigate locally. Do not copy credentials into labels or notifications.

## Exporter down

Check the exporter process, configured target and network route. Prometheus `up=0` describes a failed scrape, not a confirmed unhealthy endpoint. Restart only the exporter after checking its logs and CLI configuration.

## Monitoring missing

Check Prometheus target configuration and service discovery. The sample detects absence of the entire job and missing exposition on each configured `up` target. To detect individual targets removed from discovery, maintain a separate expected-target inventory or explicit static target list. A removed endpoint is not inferable from current exporter inventory.

## Collection failed

Check permissions, ownership, valid JSON and pending storage transactions using normal agent commands. The exporter deliberately performs no repair. Reachable HTTP with collection_success=0 is different from exporter-down; do not diagnose VPN health from missing health series.

## State missing

Confirm the compose root and state directory, then confirm the watchdog has completed an instrumented cycle. Legacy/uninitialized state requires a normal watchdog cycle. Never label absence as healthy. Monitoring reset intentionally clears observation evidence while preserving counters.

## Watchdog stale

Inspect watchdog PID/status, last attempt, last success, active cycle and logs. Failed, cancelled and partial cycles never advance successful freshness. Choose exporter --freshness-seconds above the normal interval plus cycle duration; alert hold time should exceed ordinary cycle duration. A long running cycle can produce stale evidence without process death.

## Endpoint unknown

Check configured inventory and whether the endpoint has ever completed a probe. Unknown is not unavailable. Confirm provider/network/authentication configuration without putting credentials into diagnostics. Rename and reset deliberately start endpoints unknown.

## Endpoint stale

Check observation timestamp, cycle outcome and completed flags. A stopped or hung watchdog, incomplete batch, failed probe or reset can explain stale evidence. Do not act on an old last-known healthy value.

## Endpoint failure

Investigate the specific availability, authentication or connectivity check using the endpoint's configured source. Gluetun and external proxies have different supported operations; external endpoints cannot be restarted or rotated by the Gluetun runtime. Rules record both confirmed failure and success only at fresh observations, then hold the latest sample for three minutes across normal cycle gaps. A fresh success clears the held failure immediately. Unknown samples never become healthy; expired held evidence is handled by stale/unknown alerts. Default five-minute holds suppress one transient observation. Increase the hold window only if cycle cadence requires it.

## Recovery blocked

Inspect current open/approved rotation_exhausted incidents and their recommended actions. This condition includes exhausted attempts and policy-blocked recovery, such as auth/config failures; it does not prove that a numerical budget was consumed. Correct configuration or follow explicit operator approval workflows. This gauge is current incident evidence; request counters are cumulative audit outcomes and cannot establish unresolved exhaustion or recovered endpoints.

## Tuning and notifications

Edit rule `for` durations and the `[3m]` held-observation window for your cadence. Run promtool tests after changes. Alertmanager groups by alertname/job/instance/deployment, waits 30 seconds, repeats every four hours and inhibits downstream symptoms during exporter/collection failure. Endpoint IDs remain on individual alerts but are excluded from group_by to limit notification storms. The sample receiver is local-only and sends no notifications; configure a receiver explicitly in your own deployment. Restrict target addresses to nonsensitive hostnames; Prometheus adds instance to alerts, so do not use userinfo/credential-bearing targets.
