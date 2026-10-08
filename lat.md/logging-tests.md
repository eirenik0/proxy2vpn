---
lat:
  require-code-mention: true
---
# Logging Tests

These regressions pin the structured logging contract, secret redaction, concurrency boundaries, and CLI output behavior described in [[lat.md/logging#Operational Logging]].

## JSON Schema And Compatibility

Stdlib, third-party, and bound structlog events each emit one JSON record with UTC timestamps, legacy message and uppercase level fields, positional messages, and existing extra fields.

## Quiet Defaults

Without a log file, events at INFO and ERROR from both logging APIs produce no stdout or stderr content.

## Secret Redaction

Both APIs mask credentials in proxy URLs, query parameters, messages, nested password/token/API-key fields, bytes, custom values, and rendered exception text without exposing secrets in captured output.

## Dynamic Levels And Reconfiguration

Existing loggers respond to root-level changes, and repeated configuration writes each event once to the selected file while closing the prior owned file handler.

## Context Cleanup

Scoped context restores its caller after exceptions and cancellation, discards temporary fields, and clears stale operation context during logging setup.

## Concurrent Service Context

Overlapping health checks retain distinct service and provider fields across tasks and thread probes, including a failed check; parent callbacks and batch completion retain only the shared cycle context.

## Watchdog Lifecycle Context

Cycle identifiers change between runs, health and recovery events carry compose/service/provider context, incident events carry their specific ID, and cycle summaries remain free of service or incident fields.

## Failed Cycle Cleanup

A failed watchdog cycle emits one correlated exception record with redacted text and leaves no task-local cycle or service context behind.

## Machine Readable CLI Output

Agent status and incident commands produce parseable JSON with and without file logging, even while a live status check emits diagnostic records to the configured file.

## Daemon File Logging

The daemon child uses the shared JSON file pipeline, keeps console output empty, redacts secret fields, and clears its PID when it stops.
