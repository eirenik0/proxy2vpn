---
lat:
  require-code-mention: true
---
# Agent Evidence Tests

These regressions establish the secrecy contract at storage, transmission and fallback boundaries without modifying recovery inputs.

## Bounded Copied Diagnostics

Verify idempotent sanitization drops unknown raw payloads, bounds cycles and text, retains check classifications, and leaves input objects unchanged.

## Both LLM Boundaries

Verify enrichment and investigation requests and generated responses exclude configured credentials and supported encodings, including nested issue and action data.

## Configured Values And Historical Migration

Verify state and every historical incident record are scrubbed before reuse, configuration and input models remain unchanged, and concurrent append plus atomic migration preserve all records.

## Disabled And Failing LLM Fallbacks

Verify both watchdog fallback paths remain sanitized when the LLM is disabled or raises an exception containing a configured credential.

## Configuration Sources And Encodings

Verify credential discovery from environment, Compose, control client/server files and external URL userinfo, supported encoded values, labelled secrets and authorization patterns.

## Marker-containing Credentials

Verify credentials containing the literal redaction marker and supported encodings are removed from state, historical incidents, both LLM requests and results, while repeated sanitization stays stable.

## Replacement Convergence

Verify replacing one credential cannot leave another configured credential formed from the inserted marker, and the resulting text remains idempotent.

## External Credential References

Verify arbitrary username_env and password_env references resolve to environment credential values before sanitizing history, state, both LLM requests and generated results.

## Typed Control Facts

Verify credential collisions with recognized statuses, severity, daemon modes, sources, action results, diagnostic checks and valid timestamps preserve typed facts while narrative values stay redacted.

## Scalar Compose Credentials

Verify numeric, boolean and floating-point Compose credentials use the runtime string representation for literal and encoded redaction in storage, migration and both LLM callers.

## Stable Service Identity Aliases

Verify credential-overlapping endpoint names use stable stored aliases while configuration restores live identities, preserving failure counts, incident deduplication, resolution and identity correlation after credential changes.

## Compose Credential Interpolation

Verify braced and unbraced credentials from shell and project dotenv values, precedence, defaults, alternatives, requirements, nested expressions and dollar escapes are redacted with their encodings at every evidence boundary.

## Interpolation Source Semantics

Verify quoted and multiline dotenv parsing, empty-versus-unset behavior, interpolated env-file paths, opaque shell values, and safe failure for unsupported or missing required expressions.

## Literal And Raw Env Files

Verify single-quoted credentials are literal with either assignment delimiter, raw env files retain quotes and dollar expressions without interpolation errors, and their encodings are excluded from persistence and both LLM boundaries.

## Private Identity Keys

Verify aliases resist unkeyed dictionary lookup, remain stable across processes, differ between roots, use private excluded keys, and migrate known legacy hashes while dropping unknown hashes.

## Interpolated Env Formats And Live Investigation Identities

Verify env-file format expressions select raw parsing and investigation returns restore configured identities while stored and transmitted copies retain keyed aliases.

## Env Declaration Boundaries

Verify nested environment, label and arbitrary extension keys named env_file do not trigger file reads, while actual service and supported profile declarations still collect credentials.

## Exact Marker Credentials And Renamed Identities

Verify exact marker credentials have their reversible encodings masked and active incidents migrate after Compose removes the old credential-overlapping service name.
