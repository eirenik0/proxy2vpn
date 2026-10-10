# IPRoyal Mobile Contract

The first vendor discovery scopes IPRoyal dedicated mobile to one explicitly manual request for a different exit IP, with independent request and observation outcomes.

## Evidence And Unknowns

Official API documentation establishes the POST path and token header, but not a response schema, idempotency guarantee or immediate carrier allocation.

The provider contract is recorded in docs/providers/iproyal-mobile.md with dated official sources. Stable endpoint identity is separate from observed IP and local operation IDs. Session replacement, sticky TTL, renewal, purchases and automatic vendor recovery remain outside the scoped client. The local sandbox validates client assumptions, not live carrier behavior.

## Dispatch And Storage Design

Source-aware dispatch reserves a durable resource-bound claim before network effects, preserves uncertainty and cooldown across monitoring resets, and never falls back to Gluetun.

Credential references remain configuration; tokens, rotation keys, control URLs and raw responses never enter persisted evidence or metrics labels. Same-endpoint verification compares fresh valid IP observations separately from HTTP acknowledgement and connectivity. See [[egress#Shared Egress Interface]], [[agent#Evidence Secrecy Contract]] and [[iproyal-discovery-tests#IPRoyal Discovery Tests]].
