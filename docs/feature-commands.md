# CLI command inventory

The CLI registers 48 commands in 7 groups. This inventory records command entry points, not live fleet state.

Generated from `app.add_typer` registrations and `app.command` decorators. Regenerate with `python3 scripts/build_feature_inventory.py`; check freshness with `python3 scripts/build_feature_inventory.py --check`.

See the [feature map](../lat.md/feature-map.md) for behavior, ownership, state, constraints, and regression coverage.

## profile

- [`proxy2vpn profile create`](../src/proxy2vpn/cli/commands/profile.py#L77)
- [`proxy2vpn profile add`](../src/proxy2vpn/cli/commands/profile.py#L172)
- [`proxy2vpn profile list`](../src/proxy2vpn/cli/commands/profile.py#L190)
- [`proxy2vpn profile remove`](../src/proxy2vpn/cli/commands/profile.py#L210)
- [`proxy2vpn profile delete`](../src/proxy2vpn/cli/commands/profile.py#L231)

## vpn

- [`proxy2vpn vpn add`](../src/proxy2vpn/cli/commands/vpn.py#L269)
- [`proxy2vpn vpn list`](../src/proxy2vpn/cli/commands/vpn.py#L385)
- [`proxy2vpn vpn start`](../src/proxy2vpn/cli/commands/vpn.py#L554)
- [`proxy2vpn vpn stop`](../src/proxy2vpn/cli/commands/vpn.py#L606)
- [`proxy2vpn vpn restart`](../src/proxy2vpn/cli/commands/vpn.py#L652)
- [`proxy2vpn vpn update`](../src/proxy2vpn/cli/commands/vpn.py#L695)
- [`proxy2vpn vpn logs`](../src/proxy2vpn/cli/commands/vpn.py#L747)
- [`proxy2vpn vpn delete`](../src/proxy2vpn/cli/commands/vpn.py#L820)
- [`proxy2vpn vpn test`](../src/proxy2vpn/cli/commands/vpn.py#L843)
- [`proxy2vpn vpn export-proxies`](../src/proxy2vpn/cli/commands/vpn.py#L861)
- [`proxy2vpn vpn status`](../src/proxy2vpn/cli/commands/vpn.py#L967)
- [`proxy2vpn vpn public-ip`](../src/proxy2vpn/cli/commands/vpn.py#L1000)
- [`proxy2vpn vpn dns-status`](../src/proxy2vpn/cli/commands/vpn.py#L1025)
- [`proxy2vpn vpn updater-status`](../src/proxy2vpn/cli/commands/vpn.py#L1047)
- [`proxy2vpn vpn port-forwarded`](../src/proxy2vpn/cli/commands/vpn.py#L1069)
- [`proxy2vpn vpn restore`](../src/proxy2vpn/cli/commands/vpn.py#L1090)
- [`proxy2vpn vpn restart-tunnel`](../src/proxy2vpn/cli/commands/vpn.py#L1193)

## servers

- [`proxy2vpn servers update`](../src/proxy2vpn/cli/commands/servers.py#L13)
- [`proxy2vpn servers list-providers`](../src/proxy2vpn/cli/commands/servers.py#L29)
- [`proxy2vpn servers list-countries`](../src/proxy2vpn/cli/commands/servers.py#L38)
- [`proxy2vpn servers list-cities`](../src/proxy2vpn/cli/commands/servers.py#L47)
- [`proxy2vpn servers validate-location`](../src/proxy2vpn/cli/commands/servers.py#L56)

## system

- [`proxy2vpn system init`](../src/proxy2vpn/cli/commands/system.py#L26)
- [`proxy2vpn system secure`](../src/proxy2vpn/cli/commands/system.py#L131)
- [`proxy2vpn system validate`](../src/proxy2vpn/cli/commands/system.py#L190)
- [`proxy2vpn system diagnose`](../src/proxy2vpn/cli/commands/system.py#L214)

## fleet

- [`proxy2vpn fleet plan`](../src/proxy2vpn/cli/commands/fleet.py#L10)
- [`proxy2vpn fleet deploy`](../src/proxy2vpn/cli/commands/fleet.py#L41)
- [`proxy2vpn fleet status`](../src/proxy2vpn/cli/commands/fleet.py#L63)
- [`proxy2vpn fleet rotate`](../src/proxy2vpn/cli/commands/fleet.py#L76)
- [`proxy2vpn fleet scale`](../src/proxy2vpn/cli/commands/fleet.py#L90)

## agent

- [`proxy2vpn agent run`](../src/proxy2vpn/cli/commands/agent.py#L126)
- [`proxy2vpn agent status`](../src/proxy2vpn/cli/commands/agent.py#L196)
- [`proxy2vpn agent compact-incidents`](../src/proxy2vpn/cli/commands/agent.py#L292)
- [`proxy2vpn agent incidents`](../src/proxy2vpn/cli/commands/agent.py#L332)
- [`proxy2vpn agent investigate`](../src/proxy2vpn/cli/commands/agent.py#L392)
- [`proxy2vpn agent approve`](../src/proxy2vpn/cli/commands/agent.py#L444)
- [`proxy2vpn agent dismiss`](../src/proxy2vpn/cli/commands/agent.py#L472)
- [`proxy2vpn agent stop`](../src/proxy2vpn/cli/commands/agent.py#L493)
- [`proxy2vpn agent metrics`](../src/proxy2vpn/cli/commands/agent.py#L529)

## endpoint

- [`proxy2vpn endpoint request-exit-ip`](../src/proxy2vpn/cli/commands/endpoint.py#L23)
- [`proxy2vpn endpoint provider-status`](../src/proxy2vpn/cli/commands/endpoint.py#L43)
- [`proxy2vpn endpoint reconcile`](../src/proxy2vpn/cli/commands/endpoint.py#L57)
