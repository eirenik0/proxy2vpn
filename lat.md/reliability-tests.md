---
lat:
  require-code-mention: true
---
# Reliability Tests

These regressions verify desired-state consistency, fleet resource ownership, and continuous monitoring across operational failures.

## Fleet Fresh Compose

A subsequent fleet operation reloads desired state after an independent writer commits, preserving that change and avoiding permanent conflicts from a cached manager.

## Compose Stale Writers

Independent Compose snapshots cannot overwrite newer desired state or its backup; a stale writer receives an explicit conflict and must reload before retrying.

## Compose Direct Edits And Removal

Direct document edits use the same stale-write check as typed mutations, including external text changes and deletion that must not be silently undone.

## Compose Commit Failure

Failed atomic replacement preserves the last committed file and permits an explicit retry against the same unchanged revision.
