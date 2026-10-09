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
