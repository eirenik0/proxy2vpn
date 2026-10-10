---
lat:
  require-code-mention: true
---
# Metrics Tests

Metrics tests verify cumulative counters, freshness and read-only collection under concurrent writes and damaged storage.

## Durable Request Counters

Counters survive action history truncation, stale action merges and monitoring reset, and retain the committed request result independently of rechecks.

## Freshness And Unknown Evidence

Scrapes never refresh timestamps; failed cycles and expired observations remain stale, while unknown authentication remains absent rather than false.

## Read Only Failure Boundaries

Missing state creates no storage and corrupt evidence or pending transactions report collection failure without repair or journal recovery.

## Cycle Fencing

Metric-only conflict finalization preserves concurrent state and reset generations fence obsolete cycle completions.

## Recovery Cycle Finalization

A successful cycle containing a committed recovery action finalizes the current metrics object, preserving request counters while clearing in-progress evidence and advancing success.

## Renamed Endpoints

Rotation removes the old endpoint observation and initializes the replacement as unknown until a genuine probe provides evidence.

## Failed Counter Commit

A failed counter write cannot increment a later retry twice, and indeterminate cancelled requests count as unknown.

## Partial Probe Persistence

A completed endpoint probe is durably recorded while another remains hung, and batch cancellation leaves the unfinished endpoint unknown.

## HTTP And CLI Isolation

The HTTP exporter and one-shot CLI return metrics from existing evidence without changing storage or initiating network health probes.

## Corrupt Metric Structure

Rendering overflow and duplicate endpoint/counter label tuples return only collection failure, never partially successful or duplicate Prometheus samples.

## Restart And Source Persistence

Reconstructing a producer store preserves counters and both source identities while never-observed endpoints expose unknown rather than unavailable health.
