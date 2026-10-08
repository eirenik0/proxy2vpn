---
lat:
  require-code-mention: true
---
# Gluetun Runtime Tests

These specifications cover the runtime interface and substituted orchestration results, while existing Docker, control-authentication, CLI, and recreation regressions preserve compatibility.

## Missing And Stopped Containers

Runtime inspection returns normalized missing, stopped, and running states; non-running containers skip diagnostics and control probes, and SDK objects never reach the result.

## Probe Configuration And Threads

Runtime inspection forwards log limits, probe timeouts, direct-IP evidence, control timeouts, and retry counts; synchronous dependency calls execute off the event loop and clients close.

## Explicit Inspection Failures

Inspection and diagnostic failures remain distinguishable from missing containers, including exceptions with empty messages that must still mark an incomplete assessment.

## Strict Docker Enumeration

The actual Docker lookup path distinguishes failed enumeration from an absent container, yields failed assessments during daemon outages, and preserves tolerant lookup for existing CLI callers.

## Partial Probe Failures

Direct-IP, control, and egress failures expose error text while retaining successful diagnostics so optional probes do not erase useful health evidence.

## Control Outcomes And Authentication

Control status and tunnel restart return explicit success or failure while preserving the existing environment-backed authentication, configured timeouts, and retry policy.

## Restore And Recovery Outcomes

Restoration forces recreation with the caller's exact profile and compose-relative env path, returns no SDK object, and supports subsequent successful or failed recovery observations.

## Diagnostic Evidence Failures

Evidence collection respects log limits and timeouts, normalizes log text, and retains diagnostic and raw-log outcomes independently when either operation fails.

## Cleanup Ownership And Failure

Orphan cleanup receives the caller's ComposeManager without constructing another desired-state source and returns removed names or an explicit raised failure.

## Cancellation

Every runtime operation propagates async cancellation rather than converting it to failure data, and control requests close their client when cancelled.

## Watchdog Restore Policy

With only runtime results injected, missing or stopped services preserve restoration outcomes, failed recreation errors, profile ownership, and persisted health state without Docker patches.

## Watchdog Restart Policy

With runtime results injected, successful tunnel restart recovers a service and failed restart escalates to restoration while preserving the action history and final health outcome.

## Investigation Runtime Evidence

Incident investigation consumes normalized runtime evidence and control outcomes without constructing clients or touching Docker objects, preserving diagnostics and selected log evidence.

## Watchdog Cleanup Failure

An explicit runtime cleanup failure aborts before assessment and persists the existing cycle error and cleared progress fields without Docker patches.

## Shared Assessment Runtime Results

Injected runtime observations produce compatible healthy, missing, stopped, and failed health classifications, including IP evidence and optional control failures, without extra runtime calls.
