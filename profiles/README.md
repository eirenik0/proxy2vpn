# User Profiles for proxy2vpn

This directory contains user profile files for use with the proxy2vpn script.

## Format

Each profile should be in `.env` format with VPN credentials:

```
OPENVPN_USER=username
OPENVPN_PASSWORD=password
```

## Usage

1. Create profiles using the `create-profile` command:
   ```
   ./proxy2vpn.sh create-profile myprofile username password
   ```

2. Create containers using profiles:
   ```
   ./proxy2vpn.sh create-from-profile vpn1 8888 myprofile "New York"
   ```

3. Use profiles in batches or presets for easier management.
