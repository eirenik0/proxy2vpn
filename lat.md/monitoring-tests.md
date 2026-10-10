---
lat:
  require-code-mention: true
---
# Monitoring Tests

Operator asset tests validate runbook/query wiring and current recovery intervention evidence; promtool fixtures evaluate real alert activation and clearing.

## Runbook And Dashboard Contract

Each alert links to an existing runbook, uses a nonzero hold and bounded labels; dashboard queries cover monitoring, observations, latency, incidents and request outcomes with explicit absence display.

## Current Recovery Intervention

Blocked recovery gauges count current open/approved incidents, clear after resolution and never infer numerical exhaustion from lifetime request counts.
