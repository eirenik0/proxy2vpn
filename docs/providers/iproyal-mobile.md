# IPRoyal dedicated-mobile discovery contract

Reviewed 2026-10-10. This contract selects **dedicated mobile proxies**, not the rotating residential or per-GB mobile product. It establishes a narrow manual workflow for #145. Official documentation was checked; the local sandbox validates client assumptions only. No paid order, provider account or production rotation was created or used.

## Verified documentation and limits

[API authentication](https://docs.iproyal.com/proxies/mobile/api) specifies the HTTPS reseller base `https://apid.iproyal.com/v1/reseller` and `X-Access-Token`. [Rotation API](https://docs.iproyal.com/proxies/mobile/api/proxies) specifies `POST /orders/4g/rotate-ip/{key}`. The access token and rotation key are separate secrets. That page does not define a response schema, idempotency key, operation-status API, rate-limit response contract or retry guarantee. Absence of documentation is recorded as unknown, not unsupported by the provider.

[Proxy strings](https://docs.iproyal.com/proxies/mobile/using-proxy-strings) documents authenticated HTTP/HTTPS proxying and SOCKS5 with different ports. The implementation will use existing HTTP CONNECT with verified target TLS; SOCKS5 is outside this release. Credentials are supplied via environment references, not URLs in configuration. Token authorization for the control API is separate from proxy username/password authentication.

[Mobile product documentation](https://docs.iproyal.com/proxies/mobile) describes a six-minute rotation interval. The newer [rotation FAQ](https://help.iproyal.com/en/articles/7222132-how-to-rotate-the-ips-in-mobile-proxies) describes three minutes for Lithuania and six for UK/US, and plan-specific automatic rotation. These sources differ: use a conservative local minimum of 360 seconds, configurable upward. This is a client dispatch guard, not a verified universal provider rate limit. A provider rejection or Retry-After must never shorten it.

[Troubleshooting](https://docs.iproyal.com/proxies/mobile/troubleshooting) says carrier allocation can delay a new IP up to 30 minutes and ISP behavior can rotate IPs independently. Therefore neither successful HTTP acknowledgement nor a local timeout proves a new IP, stable IP, uninterrupted connections or causation of an observed change.

[The official quick-start guide](https://iproyal.com/quick-start-guides/mobile-proxies/) distinguishes dedicated and rotating products and describes subscription extension. Expiry metadata, renewal API eligibility, exact live pricing and account/plan eligibility have not been validated. Renewal, purchases and subscription changes remain operator dashboard tasks. No billing API will be called by #145.

## Explicit unknowns and unsupported client operations

Dedicated endpoint identity means an operator-configured ID and fixed proxy host/port/credential references. No documented sticky-session TTL, renewal guarantee, session replacement API, connection-survival guarantee or provider session ID is assumed. A local operation ID is an audit correlation value, not a vendor session token. Exit IP is transient observation evidence and is never endpoint identity.

The scoped client supports only manual `request_different_exit_ip`. It does not create/replace endpoints, replace sessions, renew orders, change credentials, reboot equipment, enable vendor automatic rotation or perform Docker/Gluetun operations. Expired subscriptions and authentication rejection produce investigation evidence; credentials are not renewed automatically. Generic external proxies retain no mutation capabilities. Watchdog recovery remains investigation-only for vendor endpoints in this release.

Official rate-limit codes, success/error envelopes, Retry-After guarantees and provider-side idempotency remain unknown. Client timeouts are local policy: one total 10-second control request, no automatic retry, followed by bounded same-endpoint probes. A verification deadline is not a provider completion SLA.

## Authorization and dispatch design for #145

The operator configures vendor metadata on one external endpoint: provider literal `iproyal_mobile`, `access_token_env`, and `rotation_key_env`. These store environment-variable names only. The existing endpoint ID, connection and credential references remain unchanged. A dedicated manual CLI command names the endpoint and explicitly confirms potential connection disruption; the command never provisions paid resources. An operator may probe/reconcile without authorizing another mutation.

Dispatch selects the configured source and exact provider before checking capabilities. Unsupported source/provider/operation combinations fail before network effects. The existing generic observation seam remains intact; no multi-provider registry or framework is needed. A provider-specific adapter reuses CONNECT observations and implements one control operation. Vendor failures cannot fall through to Compose, Docker or Gluetun methods.

Before POST, validate credential references and reserve a private operation record under the existing stable evidence transaction lock. The record binds a local operation ID to an opaque HMAC endpoint identity, provider-resource fingerprint and configuration fingerprint. Resource identity must include the rotation key, so aliases cannot bypass a same-resource claim. Never persist the raw key or token. Release the lock before HTTP or probes.

Keep operation claims/cooldowns in a private, atomically replaced provider-operation artifact that ordinary monitoring reset does not clear. Start every dispatched claim with a conservative 30-minute uncertainty guard. A completed HTTP acknowledgement may reduce the guard to the configured minimum only after durable outcome persistence; timeout, cancellation, process death and indeterminate responses preserve uncertainty. Rejected requests retain at least the configured guard, with a valid bounded Retry-After extending it. Concurrent commands cannot dispatch twice against a claimed resource. A restart cannot silently retry. An explicit later operator decision is required for another request; local fencing cannot guarantee provider-side exactly-once behavior.

Use a fixed HTTPS API origin, a validated and percent-encoded rotation-key path component, certificate verification, no redirects, no ambient HTTP proxy settings and no automatic POST retries. Never include raw vendor responses, exception messages, request URLs or headers in logs, incidents or action evidence. The key is secret even though it occupies a URL path.

## Result and verification model

Record independent dimensions: request `not_dispatched`, `acknowledged`, `rejected` or `unknown`; exit observation `changed`, `unchanged` or `unknown`; post-request authentication/connectivity nullable booleans; and session replacement `unknown`/unsupported. `acknowledged` means HTTP acknowledgement only, not validated vendor completion. HTTP framing failures and demonstrably contradictory responses remain unknown. An undocumented but well-formed 2xx body establishes HTTP acknowledgement only; it is not matched against an invented provider success schema. Bounded reason codes distinguish credentials, rate limiting, expiry/configuration, timeout, cancellation and unsupported operations; raw narratives are omitted.

Take a fresh baseline through the configured proxy and address family. After the request, probe that same host, port and credential selection without constructing a different proxy or silently changing expected-IP constraints. Only two valid comparable IP observations establish changed/unchanged. No baseline or failed post-probe means unknown. A changed IP records an observation, not causation; restored connectivity requires failed connectivity before and verified connectivity afterward. A request can be acknowledged with unchanged IP or unknown connectivity.

Completion merges into current storage under the same resource/operation claim. Configuration changes prevent attributing a late result to a replacement endpoint. Persist only sanitized typed facts and opaque identities; raw IP comparison can happen transiently in memory. Existing action evidence uses source `external_proxy`; its immutable request result must not be rewritten by later probes. Monitoring reset may clear health evidence but cannot undo the external operation or its dispatch guard.

## Reproducible sandbox and live follow-up

Run `uv run --locked pytest tests/test_iproyal_discovery.py -q`. The sandbox uses a local authenticated control API and CONNECT/TLS proxy with synthetic credentials/IPs. It exercises exact POST path/header, same proxy/authentication before and after, acknowledgement with unchanged IP, acknowledgement with observed change, and timed-out request with observed change. It does not contact IPRoyal or establish actual carrier/session guarantees.

#145 must extend the real adapter sandbox to concurrent claims, cooldown persistence across process/store restart and monitoring reset, cancellation, auth rejection, rate limits, malformed responses, redirects, expiry, configuration changes and unsupported dispatch. Acceptance must assert no Docker/Gluetun mutations and credential-free storage/logs. The sandbox itself must not introduce production control-origin overrides.

A separately authorized live check requires an operator-provided dedicated order with capacity for disruption, proxy credential references, token/key references, confirmed plan/location cooldown and spending approval for any purchase. Do not purchase, extend or rotate implicitly. Record same-endpoint baseline and post-request observations over the provider's actual allocation window; stop rather than retry a request with unknown outcome. Actual price, eligibility, rate limits, account-specific response envelopes and connection survival remain live validation items, not claims from the local simulator.
