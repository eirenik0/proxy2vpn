# IPRoyal Mobile Contract

The first vendor discovery scopes IPRoyal dedicated mobile to one explicitly manual request for a different exit IP, with independent request and observation outcomes.

## Evidence And Unknowns

Official API documentation establishes the POST path and token header, but not a response schema, idempotency guarantee or immediate carrier allocation.

The provider contract is recorded in docs/providers/iproyal-mobile.md with dated official sources. Stable endpoint identity is separate from observed IP and local operation IDs. Session replacement, sticky TTL, renewal, purchases and automatic vendor recovery remain outside the scoped client. The local sandbox validates client assumptions, not live carrier behavior.

## Dispatch And Storage Design

Source-aware dispatch reserves a durable resource-bound claim before network effects, preserves uncertainty and cooldown across monitoring resets, and never falls back to Gluetun.

Credential references remain configuration; tokens, rotation keys, control URLs and raw responses never enter persisted evidence or metrics labels. Same-endpoint verification compares fresh valid IP observations separately from HTTP acknowledgement and connectivity. See [[egress#Shared Egress Interface]], [[agent#Evidence Secrecy Contract]] and [[iproyal-discovery-tests#IPRoyal Discovery Tests]].

## Manual Runtime

The manual CLI selects explicitly configured mobile endpoints, records request outcomes atomically with audit counters, and probes the same configured proxy separately.

[[src/proxy2vpn/agent/provider.py#MobileOperations]] reserves one HMAC resource claim per rotation key under the shared evidence lock. [[src/proxy2vpn/adapters/iproyal.py#IPRoyalMobileAdapter]] posts only to the fixed official HTTPS origin with no ambient proxy, redirect or retry. Generic external endpoints and Gluetun services never dispatch this operation. The watchdog remains investigation-only for external endpoints.

## Durable Uncertainty

Provider claims live in AgentState independently of monitoring generation; reset preserves them and delayed provider writes merge into fresh state by operation identity.

[[src/proxy2vpn/agent/state.py#AgentStateStore#provider_transaction]] bypasses the observation generation context only within a fresh provider transaction. Ordinary stale health writes remain fenced. Intent is durable before POST; outcome, action, request counter and audit marker commit together. Unknown outcomes retain at least 30 minutes or the longer configured cooldown. Lost intent requires explicit probe-only reconciliation after its guard; no recovery path replays a POST. Capacity rejects new resources rather than evicting guards.

## Observation Semantics

HTTP acknowledgment, exit-IP change, authentication and connectivity are independent facts, and configuration changes prevent attributing delayed observations.

One credential snapshot supplies baseline, request and post-request observations. Exit comparison requires two fresh valid IPs in the same address family; session replacement remains unknown. Reconciliation has no pre-request baseline and preserves change uncertainty. Request facts are immutable once audited. Configuration fingerprints include resolved credentials; resource claims exclude access-token changes so aliases and token changes cannot bypass cooldown. See [[mobile-operation-tests#Mobile Operation Tests]].

## Reconciliation Authorization

Reconciliation selects an existing unambiguous operation before observing configuration and never requires request-only credentials or creates a provider resource.

Missing or invalid current endpoint configuration can terminally audit retained intent and mark verification changed. Missing proxy credentials prevent probes. Phase and guard classification happen inside the atomic audit transaction. Multiple retained resources with one service name require an operation ID. Malformed inventory JSON remains a fail-closed storage boundary until repaired.
