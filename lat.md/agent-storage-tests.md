---
lat:
  require-code-mention: true
---
# Agent Storage Tests

These tests verify that watchdog evidence remains private, durable, and consistent across concurrent processes, interrupted commits, and delayed network operations.

## Concurrent Transactions

Multiple independent processes update state and append distinct incidents under the common transaction without losing updates or producing malformed history.

## Stale Writes And Action Evidence

Obsolete state and incident replacements are rejected, while completed operation audits append to fresh state without overwriting a concurrent status change.

## Reset Fences Delayed Work

Reset prevents old state, existing incidents, and newly created incidents from restoring removed evidence; a fresh generation can begin monitoring again.

## Reset Journal Recovery

An interruption after reset state replacement recovers the matching empty history on the next transaction and removes the completed journal idempotently.

## Replacement Failure

A failed atomic replacement preserves the previous valid state and removes temporary files so an interrupted update cannot corrupt the last committed snapshot.

## Partial History And Corruption

Only an invalid unterminated final history record is recovered; earlier or newline-terminated corruption is reported safely without discarding valid history.

## Private Permissions

A permissive umask cannot create public agent evidence, and accesses repair legacy directory, state, history, identity, PID, and lock permissions.

## Symlink Rejection

Symlink targets for every storage artifact are rejected without changing the linked file's content or permissions.

## Lock Timeout

A competing transaction fails within the configured bounded timeout, reports a safe retry error, and remains usable after the lock is released.

## Investigation Interleaving

Network investigation holds no storage lock, allows another writer to dismiss the incident, and rejects its stale result without reopening that dismissal.

## Fresh Incident Decisions

A watchdog holding an obsolete incident inventory rechecks the latest dismissal before creating or updating evidence, preserving the suppression cooldown.

## Approval Audit Interleaving

A completed manual rotation persists its audit against current state before stale finalization can raise a conflict, preserving the other writer's status changes.

## Approval Reset Interleaving

An interrupted approval started before reset cannot rename incidents created afterward, including when its interruption handler attempts recovery migration.

## Unsafe Log Parents

Daemon logging rejects symlink parent directories before creating a log outside the intended directory.

## Special File Rejection

A FIFO in place of a storage file is rejected immediately rather than blocking validation before the bounded lock timeout.

## Approval Entry Reset

A reset between initial approval validation and endpoint checks cannot make an old operation adopt the new generation or repopulate its audit history.

## Portable Filename Isolation

Case-insensitive aliases, reserved-name aliases, traversal paths, absolute paths and trailing-dot filenames are rejected before lock acquisition can truncate evidence.

## Concurrent Approval Claims

Two processes loading the same incident before approval perform exactly one rotation, with its claim persisted before network work and no storage lock held while waiting.

## Interrupted Approval Claim

Cancellation retains an open incident and its persistent execution claim, so another approval cannot blindly duplicate an operation with an uncertain outcome.

## Approval Completion Merge

Approval completion preserves newer incident evidence and closed states while resolving a still-open claimed incident after concurrent watchdog updates.

## Daemon Startup Conflict

A revision conflict during initial daemon state publication reloads on the next interval and reaches monitoring without losing a concurrent update.

## Unicode Filename Isolation

Canonically equivalent Unicode filenames, including case variants, are rejected before state and history can overwrite the same path on normalization-insensitive filesystems.

## Approval CLI Outcome Separation

The approval CLI reports preserved final incident status separately from execution evidence, so concurrent dismissal or resolution cannot invent a rotation outcome.

## Dedicated Directory Validation

Selecting an unrelated project directory fails before any directory or file permissions change, including when known-looking files precede unknown entries.

## Identity Directory Validation

Direct evidence-sanitizer identity setup validates the dedicated storage directory before creating locks, keys, or changing permissions of unrelated files.

## Crash Temporary Permission Repair

Recognized current and legacy crash temporaries become private without deletion, preserving files that another process may still be using.

## Custom Artifact Directory Validation

Configured storage filenames remain usable when identity setup validates the shared directory and both persistence paths perform sanitization.

## Windows Reserved Components

Windows devices, extended console names, alternate-stream delimiters, forbidden characters, and controls are rejected in both directory and file settings on all platforms.

## Alternate Storage Identity Compatibility

An alternate state directory with custom filenames can coexist with populated canonical storage while retaining the same identity key and leaving both histories usable.
