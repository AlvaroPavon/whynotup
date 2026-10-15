"""Read-only, deliberately bounded checks of a resolved Compose project.

Connection settings are conventions, not proof of what an application does.
The network checks therefore require an identifiable peer and are reported as
probable configuration problems, never as confirmed runtime root causes.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import ComposeProject, Evidence, Finding, SourceLocation, Suggestion


@dataclass(frozen=True)
class _Endpoint:
    key: str
    host: str
    port: int | None
    port_key: str | None
    family: str | None
    url: bool = False


_FAMILIES = {
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres",
    "mysql": "mysql", "mariadb": "mysql", "mongo": "mongo", "mongodb": "mongo",
    "redis": "redis", "cache": "redis", "rabbitmq": "rabbitmq", "amqp": "rabbitmq",
    "mssql": "mssql", "sqlserver": "mssql", "db": "database", "database": "database",
}
_SCHEMES = {
    "postgres": "postgres", "postgresql": "postgres", "mysql": "mysql",
    "mariadb": "mysql", "mongodb": "mongo", "mongodb+srv": "mongo", "redis": "redis",
    "rediss": "redis", "amqp": "rabbitmq", "amqps": "rabbitmq", "mssql": "mssql",
}
_DATABASE_FAMILIES = {"postgres", "mysql", "mongo", "mssql"}
_MODES = {"ro", "rw", "z", "Z", "cached", "delegated", "consistent", "nocopy"}


def diagnose(project: ComposeProject) -> list[Finding]:
    """Return additional findings; loader findings are not duplicated."""
    findings: list[Finding] = []
    for name, service in project.services.items():
        findings.extend(_bind_mounts(project, name, service))
        findings.extend(_dependencies(project, name, service))
        for endpoint in _endpoints(project.service_environment.get(name, {})):
            loopback = _is_loopback(endpoint.host)
            peer = (_infer_peer(project, name, endpoint) if loopback else
                    _peer_for_hostname(project, name, endpoint.host))
            if peer is None:
                continue
            if loopback:
                finding = _loopback_finding(project, name, peer, endpoint)
                if finding is not None:
                    findings.append(finding)
            # A published-port comparison requires a service-to-service network
            # path; host and unknown external namespaces do not have that path.
            if _has_common_network(project, name, peer):
                finding = _published_port_finding(project, name, peer, endpoint)
                if finding is not None:
                    findings.append(finding)
    return findings


def _evidence(summary: str, location: SourceLocation) -> Evidence:
    return Evidence(summary, location.file, location.line)


def _env_location(project: ComposeProject, service: str, key: str) -> SourceLocation:
    explicit = project.locations.get(("service_environment", service, key))
    if explicit is not None:
        return explicit
    environment = project.raw_services.get(service, {}).get("environment", {})
    if isinstance(environment, list):
        for index, value in enumerate(environment):
            if isinstance(value, str) and value.split("=", 1)[0] == key:
                return project.location(service, "environment", index)
    if isinstance(environment, dict) and key in environment:
        return project.location(service, "environment", key)
    return project.location(service, "env_file")


def _bind_source(volume: Any) -> tuple[str, bool] | None:
    if isinstance(volume, dict):
        if volume.get("type") != "bind" or not isinstance(volume.get("source"), str):
            return None
        bind = volume.get("bind") or {}
        create = not (isinstance(bind, dict) and bind.get("create_host_path") is False)
        return volume["source"], create
    if not isinstance(volume, str):
        return None
    # Keep a Windows drive colon intact, including in a short Compose mount.
    drive = bool(re.match(r"^[A-Za-z]:[\\/]", volume))
    parts = volume[2:].split(":") if drive else volume.split(":")
    if drive:
        parts[0] = volume[:2] + parts[0]
    if len(parts) < 2:
        return None  # anonymous volume, with only a container target
    if len(parts) == 2 and all(mode in _MODES for mode in parts[1].split(",")):
        return None  # e.g. /data:ro, an anonymous volume with a mode
    source = parts[0]
    if not (source.startswith((".", "/", "~")) or "/" in source or "\\" in source or drive):
        return None  # named volume
    return source, True


def _bind_mounts(project: ComposeProject, name: str, service: dict[str, Any]) -> list[Finding]:
    findings = []
    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        return findings
    for index, volume in enumerate(volumes):
        parsed = _bind_source(volume)
        if parsed is None:
            continue
        source, create = parsed
        if not source or "${" in source:
            continue
        # We cannot establish existence of another OS's paths on this host.
        if os.name != "nt" and (re.match(r"^[A-Za-z]:[\\/]", source) or source.startswith("\\\\")):
            continue
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = project.base_dir / path
        try:
            exists = path.exists()
        except (OSError, ValueError):
            continue
        if exists:
            continue
        location = project.location(name, "volumes", index, "source") if isinstance(volume, dict) else project.location(name, "volumes", index)
        consequence = (
            "The bind mount disables host-path creation, so a local Docker daemon cannot mount this missing path."
            if not create else
            "Compose can create a directory for this path. If you intended to mount an existing file or directory, that may leave the container with the wrong contents."
        )
        findings.append(Finding(
            code="WNU003", severity="warning" if create else "error",
            title="Host bind-mount source does not exist", service=name,
            explanation=f"The source {source!r} is missing on this host. {consequence} A remote Docker daemon may have a different filesystem.",
            evidence=[_evidence(f"Bind source {source!r}; checked relative to {project.base_dir} when not absolute.", location)],
            suggestion=Suggestion("Restore the intended file or directory, or correct the bind source. Check its expected type before creating anything.", location.file, location.line),
        ))
    return findings


def _dependency_names(service: dict[str, Any]) -> list[str]:
    depends = service.get("depends_on", {})
    if isinstance(depends, dict):
        return [name for name in depends if isinstance(name, str)]
    if isinstance(depends, list):
        return [name for name in depends if isinstance(name, str)]
    return []


def _dependencies(project: ComposeProject, name: str, service: dict[str, Any]) -> list[Finding]:
    findings = []
    depends = service.get("depends_on", {})
    for index, dependency in enumerate(_dependency_names(service)):
        settings = depends.get(dependency, {}) if isinstance(depends, dict) else {}
        if isinstance(settings, dict) and settings.get("required") is False:
            continue
        if dependency in project.services:
            continue
        location = project.location(name, "depends_on", dependency if isinstance(depends, dict) else index)
        if dependency in project.raw_services:
            profiles = project.raw_services[dependency].get("profiles", [])
            profile_names = [item for item in profiles if isinstance(item, str)] if isinstance(profiles, list) else []
            profile_text = ", ".join(profile_names) or "its declared profile"
            explanation = f"Required dependency {dependency!r} is defined but excluded from the selected services. Its profiles are {profile_text}."
            suggestion = f"Enable the appropriate profile for {dependency!r} (for example --profile {profile_names[0]}) or revise the dependency if it is not required." if profile_names else f"Include {dependency!r} in the selected project or revise the dependency if it is not required."
            title = "Required dependency is inactive"
        else:
            explanation = f"Service {name!r} requires {dependency!r}, but the selected Compose file defines no service with that name."
            suggestion = "Correct the dependency name or add the missing service definition to the Compose file."
            title = "Required dependency is not defined"
        findings.append(Finding(
            code="WNU008", severity="error", title=title, service=name,
            explanation=explanation,
            evidence=[_evidence(f"depends_on includes required service {dependency!r}.", location)],
            suggestion=Suggestion(suggestion, location.file, location.line),
        ))
    return findings


def _port(value: Any) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if 0 < result <= 65535 else None


def _endpoints(environment: dict[str, str]) -> list[_Endpoint]:
    result = []
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str) or "${" in value:
            continue
        upper = key.upper()
        # URLs must actually be connection settings, rather than arbitrary text
        # containing a URL. Passwords and userinfo are never retained in evidence.
        if upper.endswith(("_URL", "_URI")) and "://" in value:
            try:
                parsed = urlsplit(value)
                host = parsed.hostname
                scheme = parsed.scheme.split("+", 1)[0]
                family = _SCHEMES.get(parsed.scheme) or _SCHEMES.get(scheme)
                if host and (family is not None or upper.startswith(("API_", "SERVICE_"))):
                    result.append(_Endpoint(key, host, parsed.port, key, family, True))
            except ValueError:
                pass
            continue
        if upper == "PGHOST":
            family, port_key = "postgres", "PGPORT"
        elif upper.endswith("_HOST"):
            prefix = upper[:-5].lower()
            family, port_key = _FAMILIES.get(prefix), upper[:-5] + "_PORT"
        else:
            continue
        host = value.strip()
        if not host or any(char in host for char in "/ @"):
            continue
        # Plain HOST values do not include ports; a separate *_PORT is used.
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        actual_port_key = next((candidate for candidate in environment if candidate.upper() == port_key), None)
        result.append(_Endpoint(key, host, _port(environment.get(actual_port_key)) if actual_port_key else None, actual_port_key, family))
    return result


def _is_loopback(host: str) -> bool:
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _service_family(name: str, service: dict[str, Any]) -> str | None:
    image = service.get("image", "")
    if isinstance(image, str):
        image_name = image.split("@", 1)[0].split("/")[-1].split(":", 1)[0].lower()
        if image_name in _FAMILIES and _FAMILIES[image_name] != "database":
            return _FAMILIES[image_name]
        if image_name.startswith("postgres"):
            return "postgres"
        if image_name in {"mssql-server", "mssql-server-linux"}:
            return "mssql"
    return _FAMILIES.get(name.lower())


def _infer_peer(project: ComposeProject, name: str, endpoint: _Endpoint) -> str | None:
    candidates = []
    prefix = re.sub(r"_(?:HOST|URL|URI)$", "", endpoint.key.upper()).lower()
    for peer, config in project.services.items():
        if peer == name:
            continue
        family = _service_family(peer, config)
        exact_name = prefix == peer.lower()
        family_match = endpoint.family == family or (endpoint.family == "database" and family in _DATABASE_FAMILIES)
        if exact_name or (endpoint.family is not None and family_match):
            candidates.append(peer)
    dependencies = set(_dependency_names(project.services[name]))
    preferred = [peer for peer in candidates if peer in dependencies]
    candidates = preferred or candidates
    return candidates[0] if len(candidates) == 1 else None


def _namespace(project: ComposeProject, name: str) -> tuple[str, str] | None:
    visited = set()
    while name not in visited:
        visited.add(name)
        config = project.services.get(name)
        if config is None:
            return None
        mode = config.get("network_mode", "")
        if not isinstance(mode, str):
            return None
        if mode == "host":
            return ("host", "host")
        if mode.startswith("container:"):
            return None  # external namespace cannot be identified statically
        if mode.startswith("service:"):
            name = mode.split(":", 1)[1]
            continue
        return ("none" if mode == "none" else "service", name)
    return None


def _network_names(project: ComposeProject, name: str) -> set[str]:
    namespace = _namespace(project, name)
    if namespace is None or namespace[0] != "service":
        return set()
    config = project.services[namespace[1]]
    if config.get("network_mode"):
        return set()  # engine network mode is not a Compose DNS network
    networks = config.get("networks")
    if networks is None:
        return {"default"}
    if isinstance(networks, (dict, list)):
        return {key for key in networks if isinstance(key, str)}
    return set()


def _has_common_network(project: ComposeProject, name: str, peer: str) -> bool:
    return bool(_network_names(project, name) & _network_names(project, peer))


def _peer_for_hostname(project: ComposeProject, name: str, hostname: str) -> str | None:
    candidates = []
    host = hostname.rstrip(".").lower()
    for peer, config in project.services.items():
        if peer == name or not _has_common_network(project, name, peer):
            continue
        namespace = _namespace(project, peer)
        if namespace is None or namespace[1] != peer:
            # A service sharing another container's namespace has no separate
            # Compose network endpoint or DNS alias of its own.
            continue
        aliases = {peer.lower()}
        container_name = config.get("container_name")
        if isinstance(container_name, str):
            aliases.add(container_name.lower())
        networks = config.get("networks", {})
        if isinstance(networks, dict):
            common = _network_names(project, name) & _network_names(project, peer)
            for network in common:
                settings = networks.get(network) or {}
                if isinstance(settings, dict) and isinstance(settings.get("aliases"), list):
                    aliases.update(alias.lower() for alias in settings["aliases"] if isinstance(alias, str))
        if host in aliases:
            candidates.append(peer)
    return candidates[0] if len(candidates) == 1 else None


def _loopback_finding(project: ComposeProject, name: str, peer: str, endpoint: _Endpoint) -> Finding | None:
    source_namespace = _namespace(project, name)
    target_namespace = _namespace(project, peer)
    if (source_namespace is None or target_namespace is None or source_namespace[0] == "host"
            or target_namespace[0] == "host" or source_namespace == target_namespace):
        return None
    location = _env_location(project, name, endpoint.key)
    reachable = _has_common_network(project, name, peer)
    destination = target_namespace[1]
    description = f"If {peer!r} is the intended destination, change the effective host in {endpoint.key} to {destination!r}. Preserve any variable interpolation in the declaration."
    if not reachable:
        description = f"Verify the intended destination and connect {name!r} and {peer!r} to a common Compose network before using {peer!r} as the host."
    return Finding(
        code="WNU005", severity="warning", title="Loopback host points back into this container",
        service=name, confidence="probable",
        explanation=f"{endpoint.key} selects {endpoint.host!r}. In this network namespace, loopback addresses this container rather than the separate {peer!r} service. The peer is inferred from connection-setting conventions and the service definitions; application behavior has not been verified.",
        evidence=[_evidence(f"Effective {endpoint.key} host is {endpoint.host!r}.", location),
                  _evidence(f"Candidate destination {peer!r} has a separate network namespace.", project.location(peer))],
        suggestion=Suggestion(description, location.file, location.line, endpoint.host if reachable else None, destination if reachable else None),
    )


def _published_ports(project: ComposeProject, peer: str) -> list[tuple[int, int, SourceLocation]]:
    result = []
    ports = project.services[peer].get("ports", [])
    if not isinstance(ports, list):
        return result
    for index, item in enumerate(ports):
        if isinstance(item, dict):
            if item.get("protocol", "tcp") != "tcp":
                continue
            published, target = _port(item.get("published")), _port(item.get("target"))
        elif isinstance(item, str):
            value, _, protocol = item.partition("/")
            if protocol not in {"", "tcp"}:
                continue
            # Only the last two fields are relevant, including bracketed IPv6.
            fields = value.rsplit(":", 2)
            if len(fields) < 2:
                continue
            published, target = _port(fields[-2]), _port(fields[-1])
        else:
            continue
        if published is not None and target is not None:
            result.append((published, target, project.location(peer, "ports", index)))
    return result


def _published_port_finding(project: ComposeProject, name: str, peer: str, endpoint: _Endpoint) -> Finding | None:
    if endpoint.port is None:
        return None
    mappings = _published_ports(project, peer)
    exposed = project.services[peer].get("expose", [])
    exposed_ports = {_port(str(item).split("/", 1)[0]) for item in exposed} if isinstance(exposed, list) else set()
    if endpoint.port in {target for _, target, _ in mappings} or endpoint.port in exposed_ports:
        return None
    matches = [(target, location) for published, target, location in mappings if published == endpoint.port and target != published]
    targets = {target for target, _ in matches}
    if len(targets) != 1:
        return None
    target = targets.pop()
    declaration = matches[0][1]
    key = endpoint.port_key or endpoint.key
    location = _env_location(project, name, key)
    return Finding(
        code="WNU006", severity="warning", title="Container connection uses a published host port",
        service=name, confidence="probable",
        explanation=f"{key} selects port {endpoint.port}, which {peer!r} publishes on the host for container port {target}. Connections across the common Compose network normally use the container target port. This comparison does not verify which ports the application actually listens on.",
        evidence=[_evidence(f"Effective {key} connection port is {endpoint.port}; candidate peer is {peer!r}.", location),
                  _evidence(f"{peer!r} publishes TCP host port {endpoint.port} to container target {target}.", declaration)],
        suggestion=Suggestion(f"If this connection is intended for {peer!r}, change the effective port in {key} to {target}. Preserve any variable interpolation and URL credentials.", location.file, location.line, str(endpoint.port), str(target)),
    )
