# User Profiles for proxy2vpn

This directory contains `.env` files with VPN credentials. Each profile is
registered with the CLI so it can be referenced when creating VPN services.

## Format

Each profile file uses simple `KEY=value` pairs, for example:

```
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=expressvpn
OPENVPN_USER=username
OPENVPN_PASSWORD=password
```

`VPN_TYPE` may be `openvpn` or `wireguard` and defaults to `openvpn` if omitted.

## Usage

1. Create a profile file and register it:
   ```bash
   proxy2vpn profile create myprofile profiles/myprofile.env
   ```

2. Use the profile when creating services:
   ```bash
   proxy2vpn vpn create vpn1 myprofile --port 8888
   ```
