"""Validation utilities for docker-compose files used by proxy2vpn."""

from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode

from .server_manager import ServerManager


class ValidationError(Exception):
    """Raised when validation of a compose file fails."""


REQUIRED_TOP_LEVEL_KEYS = ["services"]
PROFILE_REQUIRED_FIELDS = ["image", "cap_add", "devices", "env_file"]
SERVICE_REQUIRED_FIELDS = ["ports", "environment", "labels"]
LABEL_REQUIRED_FIELDS = ["vpn.type", "vpn.port", "vpn.profile"]


def _parse_yaml(path: Path):
    yaml = YAML(typ="rt")
    text = path.read_text(encoding="utf-8")
    data = yaml.load(text)
    node = yaml.compose(text)
    return data, node


def validate_compose(
    path: Path, server_manager: ServerManager | None = None
) -> list[str]:
    """Validate a docker compose file and return a list of errors."""

    errors: list[str] = []
    try:
        data, node = _parse_yaml(path)
    except Exception as exc:  # pragma: no cover - error path
        return [f"YAML syntax error: {exc}"]

    if server_manager is None:
        try:
            server_manager = ServerManager()
            server_manager.update_servers()
        except Exception:  # pragma: no cover - best effort
            server_manager = None

    # Top level keys
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"Missing top-level key: {key}")

    # Extract profiles from node to inspect anchors
    profiles: dict[str, MappingNode] = {}
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value.startswith(
            "x-vpn-base-"
        ):
            name = key_node.value[len("x-vpn-base-") :]
            profiles[name] = value_node
            expected_anchor = f"vpn-base-{name}"
            anchor = value_node.anchor if value_node.anchor else None
            if anchor != expected_anchor:
                errors.append(f"Profile '{name}' missing anchor '&{expected_anchor}'")
            # Required fields
            profile_data = data.get(key_node.value, {})
            for field in PROFILE_REQUIRED_FIELDS:
                if field not in profile_data:
                    errors.append(f"Profile '{name}' missing field '{field}'")
                elif field == "env_file":
                    env_files = profile_data[field]
                    # env_file can be a string or a list
                    if isinstance(env_files, str):
                        env_files = [env_files]
                    for env_file in env_files:
                        env_path = Path(env_file)
                        if not env_path.exists():
                            errors.append(
                                f"Profile '{name}' env_file '{env_path}' not found"
                            )

    services_node: MappingNode | None = None
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == "services":
            services_node = value_node
            break
    services_data = data.get("services", {})
    used_profiles: set[str] = set()
    ports_seen: dict[int, str] = {}

    if services_node is not None:
        for svc_key_node, svc_node in services_node.value:
            svc_name = svc_key_node.value
            svc_data = services_data.get(svc_name, {})
            # check merge for profile
            profile_name = None
            for k_node, v_node in svc_node.value:
                if k_node.tag == "tag:yaml.org,2002:merge":
                    anchor = v_node.anchor if v_node.anchor else ""
                    if anchor.startswith("vpn-base-"):
                        profile_name = anchor[len("vpn-base-") :]
            if not profile_name:
                errors.append(
                    f"Service '{svc_name}' missing profile merge (<<: *vpn-base-<profile>)"
                )
            else:
                if profile_name not in profiles:
                    errors.append(
                        f"Service '{svc_name}' references unknown profile '{profile_name}'"
                    )
                used_profiles.add(profile_name)

            # required service fields
            for field in SERVICE_REQUIRED_FIELDS:
                if field not in svc_data:
                    errors.append(f"Service '{svc_name}' missing field '{field}'")
            # labels
            labels = svc_data.get("labels", {})
            for label in LABEL_REQUIRED_FIELDS:
                if label not in labels:
                    errors.append(f"Service '{svc_name}' missing label '{label}'")

            # port mappings and duplicates
            for p in svc_data.get("ports", []) or []:
                try:
                    # Format can be:
                    # - "8888:8888"
                    # - "0.0.0.0:8888:8888"
                    # - "0.0.0.0:8888:8888/tcp"
                    parts = p.split(":")
                    if len(parts) == 2:
                        # Simple format: "8888:8888"
                        host_port = int(parts[0])
                        int(parts[1].split("/")[0])
                    elif len(parts) == 3:
                        # Full format: "0.0.0.0:8888:8888" or "0.0.0.0:8888:8888/tcp"
                        host_port = int(parts[1])
                        int(parts[2].split("/")[0])
                    else:
                        raise ValueError(f"Invalid port format: {p}")
                except Exception:
                    errors.append(f"Service '{svc_name}' invalid port mapping '{p}'")
                    continue
                other = ports_seen.get(host_port)
                if other and other != svc_name:
                    errors.append(
                        f"Duplicate port {host_port} used by services '{other}' and '{svc_name}'"
                    )
                else:
                    ports_seen[host_port] = svc_name

            if server_manager is not None:
                env_dict: dict[str, str] = {}
                env_entries = svc_data.get("environment", []) or []
                if isinstance(env_entries, dict):
                    env_dict = {str(k): str(v) for k, v in env_entries.items()}
                else:
                    for item in env_entries:
                        if isinstance(item, str) and "=" in item:
                            k, v = item.split("=", 1)
                            env_dict[k] = v
                provider = labels.get("vpn.provider") or env_dict.get(
                    "VPN_SERVICE_PROVIDER"
                )
                city = env_dict.get("SERVER_CITIES")
                country = env_dict.get("SERVER_COUNTRIES")
                location = None
                if city and country:
                    location = f"{city},{country}"
                elif city:
                    location = city
                elif country:
                    location = country
                if (
                    location
                    and provider
                    and not server_manager.validate_location(provider, location)
                ):
                    errors.append(
                        f"Service '{svc_name}' invalid location '{location}' for {provider}"
                    )

    # Orphaned profiles
    for name in profiles:
        if name not in used_profiles:
            errors.append(f"Profile '{name}' is not used by any service")

    return errors
