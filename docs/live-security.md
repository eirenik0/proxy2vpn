# Live secure-defaults validation and migration

Issue #140's opt-in suite runs real Gluetun v3.41.1 and curl 8.19.0, each pinned to a multi-platform OCI digest in `tests/test_live_security.py`. It creates private temporary workspaces, unique Compose projects and separate probe bridges. It never selects a repository VPN profile automatically or changes an existing fleet.

## Run the live suite

Use Docker with Compose v2, host curl, NET_ADMIN and `/dev/net/tun` support, an explicitly selected working Gluetun VPN environment file, and a private IPv4 address actually assigned to the Docker host. The profile must be self-contained: env values, no references to extra WireGuard/OpenVPN files. The selected VPN account must permit two simultaneous test tunnels. Each scenario cleans up before the next starts. The suite overrides only its proxy/control configuration and runs Gluetun as root so its read-only mount can read owner-only authentication files.

```sh
export PROXY2VPN_LIVE_SECURITY=1
export PROXY2VPN_LIVE_PROFILE=/private/path/vpn.env
export PROXY2VPN_LIVE_PRIVATE_IP=192.168.1.20
unset GLUETUN_CONTROL_AUTH
make test-live-security
```

Only paths and the address belong in these variables; do not paste credentials into shell commands. The profile is copied into a mode-0700 temporary directory as a mode-0600 file. Docker itself holds VPN credentials in container environment metadata, so access to the Docker daemon remains privileged. The suite captures and discards container logs, subprocess error bodies, and control-response bodies; it does not export them as test artifacts.

Ordinary `make test` explicitly reports four skipped live cases. A skip is **not a live pass**. `make test-live-security` enables strict mode: missing Docker, credentials, a host address, or opt-in configuration fails the run. Image pulls, unsuccessful authentication, VPN startup, unreachable private publication, unexpected localhost exposure, or cleanup failures fail the run as well.

The manually dispatched **Live Security Validation** workflow is the release execution point. Configure the protected `live-security` GitHub environment with secret `VPN_PROFILE` containing a dedicated test profile. Run it on the proposed release commit before approving a release, and require all four cases to pass. The runner derives its own private host address. The JUnit result at `/tmp/proxy2vpn-live-security.xml` records the exact image digests, platform and local image IDs; it contains fixed diagnostic messages and no response bodies. Unit CI does not establish live acceptance. No raw Docker or credential artifacts are uploaded.

## Reachability and platform scope

The remote probe container uses its own bridge and the host's actual private address. A successful private-bound proxied HTTPS request calibrates that path before localhost refusal is accepted; after returning to a private binding the same path must work again. A nonexistent control route returning 404 does not count as an authorization denial.

Linux Docker publication follows the host interface binding. Docker Desktop and OrbStack forward ports through a VM; their exposure settings can change the result. Do not alter those settings to make a test pass. A container probe proves this distinct client path, not every physical LAN path. Before a Mac deployment's release acceptance, repeat the private-positive / localhost-refused / private-positive connection checks from a separate LAN device or VM using the same host address and published port. Record that external result with the release validation. See [Docker port publication](https://docs.docker.com/engine/network/port-publishing/) and [OrbStack networking](https://docs.orbstack.dev/docker/network).

The harness recreates through `docker compose -p <unique-project> ...`, because the repository's SDK recreation currently uses `proxy2vpn_network`. A unique Compose project does not isolate `vpn update --all` from that shared network. Testing the SDK command itself requires a disposable Docker daemon; do not run it against a shared fleet merely to satisfy this suite.

## Migrate an operator deployment

Plan for downtime while each VPN service is recreated and reconnects. Preserve remote clients by selecting an explicit private host address they can reach; localhost removes remote access. Keep an existing shell or management channel independent of the proxy while performing migration.

1. Stop configuration writers and save the current Compose, `control-server-auth.toml`, and `control-client-auth.json` together in a private backup directory. Record if either auth file was absent. Preserve their modes and keep credentials out of terminal output. Existing `.bak` files may describe an earlier migration; do not overwrite your independent rollback snapshot.
2. Run `proxy2vpn --compose-file /path/compose.yml system secure --proxy-bind-address 192.168.1.20 --replace-control-auth`. Omit replacement when retaining intentional custom auth. Preparation changes files only; the running service keeps its previous authentication and binding until recreation.
3. Verify every control-auth volume refers to the selected Compose directory and that the container user can read the mode-0600 server-auth file. Root containers can read it; a non-root deployment needs an ownership/ACL plan. Do not make the client credential file world-readable. An existing single-file bind mount must be recreated to see the new inode after atomic replacement. The suite exercises this shipped mount path without mounting client credentials, profiles or backups into the auth directory.
4. Recreate the selected deployment's services using its existing operational procedure. `vpn update --all` is suitable only when targeting the intended deployment on its own daemon/network. For an isolated Compose deployment, use its original project name with `docker compose -p PROJECT -f /path/compose.yml up -d --force-recreate`.
5. Probe freshly after recreation: monitor GET status succeeds, monitor PUT is denied, operator PUT succeeds, both roles are denied settings, and a proxied HTTPS request succeeds at the selected address. Existing remote clients need the new address. Repeat the command only after confirming the files and running services agree; generated credentials should not rotate on an idempotent rerun.

Authentication semantics and existing routes come from the [Gluetun control-server documentation](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md). The suite asserts the pinned version's actual responses.

## Roll back

Stop configuration writers. Restore the saved Compose **and both authentication-file states together**, including removing a client file that was originally absent; restore owner-only modes. Recreate services again with the same project/deployment procedure, because restoring files alone does not change a running service. Freshly confirm the old authentication, original binding reachability and proxied HTTPS behavior. Keep the backup private until the deployment is confirmed healthy.

The live migration cases exercise legacy unauthenticated and custom basic authentication, both backup paths, repeated migration, actual container replacement, generated authentication, and rollback including an originally absent client file. Cleanup attempts every test's unique projects/networks and deletes private scratch files even when resource cleanup fails. Removing host files does not revoke credentials held by a leftover container; cleanup failure still requires resource removal. If cleanup fails, the test fails and reports the stage; identify remaining resources by their `proxy2vpn.live` label or `p2v-live-` project and remove only that test's resources. Never use global prune commands.
