---
lat:
  require-code-mention: true
---
# IPRoyal Discovery Tests

An authenticated local control API and CONNECT/TLS proxy validate the scoped client assumptions without provisioning paid resources or contacting the provider.

## Independent Request And Observation

The same configured proxy and authentication are probed before and after one POST; acknowledgement can leave IP unchanged, while a timed-out request can still be followed by observed change.
