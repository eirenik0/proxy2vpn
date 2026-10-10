---
lat:
  require-code-mention: true
---
# Mobile Operation Tests

Local provider tests verify durable claims, truthful observations and bounded HTTP parsing without mutating real orders or production services.

## Acknowledgment And Reset

A successful HTTP acknowledgment may leave the exit unchanged; monitoring reset preserves the claim and source-aware audit, and restarted commands obey cooldown.

## Long Cooldown

Unknown outcomes respect configured cooldown above thirty minutes, and repeated reconciliation probes never duplicate requests or audit counters.

## Key Confinement

Dot segments, separators, controls and invalid Unicode rotation keys are rejected before claims or effects to prevent URL normalization changing the control route.

## Cancellation

Cancellation after dispatch intent retains unknown outcome and a durable guard rather than permitting a retry.

## Alias Concurrency

Concurrent endpoint aliases of the same rotation key share a resource claim and cannot dispatch twice.

## Expiry

Operator-supplied expiry metadata blocks a request before any claim or provider effect.

## Lost Dispatch

A process death after durable intent requires explicit reconciliation after the uncertainty guard, and that reconciliation never replays the request.

## Failed Outcome Commit

A failed atomic outcome write retains durable dispatch uncertainty without persisting a partial action or incrementing counters.

## Changed Credentials

A credential change fences late verification while preserving the immutable HTTP acknowledgment and request reason.

## Reset Generation

Provider completion merges into fresh reset state, while delayed ordinary health writers retain the generation fence.

## Unsupported Sources

Generic external endpoints, Gluetun names and missing endpoints cannot invoke the mobile provider operation.

## Actual HTTP Client

The production HTTP client handles fragmented, oversized, contradictory, malformed, non-standard numeric constants, rejected and redirect responses through local listeners with no retry or secret evidence.

## Actual Proxy Verification

The complete manual workflow probes the same authenticated CONNECT configuration over verified TLS before and after one local control request, independently reporting acknowledgment and exit change.

## Revoked Reservation

Reconciliation terminally revokes an expired reservation so a suspended baseline task cannot later dispatch or rewrite the audited outcome.

## Durable Dispatch Required

Advertising a manual provider capability never lets the generic execute entry point silently acknowledge or bypass the durable coordinator.

## Revoked Control Credentials

Missing tokens or keys cannot block terminal audit of an expired claim; missing proxy credentials or endpoints prevent probes and preserve unknown attribution.

## Removed Verification Configuration

Endpoint removal or invalid provider schema after HTTP acknowledgment records configuration_changed without rewriting immutable request facts.

## Reconciliation Claim Race

Reconciliation derives phase and deadline from the locked current claim so a concurrent dispatch cannot be misclassified or prematurely audited.

## Ambiguous Operation Names

Several resources can retain one service name after key changes; reconciliation requires an explicit operation ID instead of guessing ownership.

## Large Retry Delays

Arbitrary-length ASCII delta-seconds are safely clamped to the maximum guard, while invalid values are rejected and zero padding preserves numeric meaning.

## Portable JSON Bound

A fixed nesting limit is enforced independently of Python decoder behavior, excluding delimiters inside quoted and escaped strings.
