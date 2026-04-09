# Proxy2VPN

Proxy2VPN turns Docker-managed Gluetun containers into a compose-root VPN fleet with shared profiles, health analysis, and automated remediation.

## Documentation Map

This index points to the stable concepts that explain how the CLI, compose state, fleet planner, diagnostics, and watchdog fit together.

- [[architecture]]: CLI context, compose-root ownership, and the profile/service domain model.
- [[health]]: Diagnostics, normalized health assessment, and provider-scoped rotation memory.
- [[fleet]]: Deployment planning, slot allocation, and rotation execution.
- [[agent]]: Watchdog cycle behavior and compose-root incident state.
