"""Read-only Docker snapshots and bounded checks of local published TCP ports."""
from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .models import Check, ComposeProject, Evidence, Finding, RuntimeSnapshot, Suggestion


_TIMEOUT = 10
_MAX_CONTAINERS = 128
_MAX_RANGE = 32
_MAX_PORT_PROBES = 128
_HEX_ID = re.compile(r"^[a-fA-F0-9]{12,64}$")
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
# The Docker daemon returns only these fields; credentials and logs are never requested.
_INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},'
    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"state":{{json .State.Status}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
    '"exit_code":{{json .State.ExitCode}},"ports":{{json .NetworkSettings.Ports}}}'
)


def _unavailable(reason: str) -> RuntimeSnapshot:
    return RuntimeSnapshot(False, reason=reason, checks=[Check("docker-runtime", "unavailable", reason)])


def _run(argv: list[str], project: ComposeProject) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=project.base_dir, capture_output=True, text=True,
        timeout=_TIMEOUT, shell=False, check=False,
        env=_effective_environment(project),
    )


def _effective_environment(project: ComposeProject) -> dict[str, str]:
    return {
        **os.environ, **project.environment,
        "COMPOSE_PROFILES": ",".join(project.active_profiles),
    }


def _endpoint_is_local(endpoint: Any) -> bool | None:
    if not isinstance(endpoint, str) or not endpoint:
        return None
    try:
        parsed = urlsplit(endpoint)
        if parsed.scheme in {"unix", "npipe"}:
            return True
        if parsed.scheme != "tcp" or parsed.hostname is None:
            return False if parsed.scheme in {"ssh", "http", "https"} else None
        if parsed.hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            return False
    except ValueError:
        return None


def _local_daemon(project: ComposeProject) -> bool | None:
    """Classify the selected endpoint without retaining its host/path or credentials."""
    environment = _effective_environment(project)
    context = environment.get("DOCKER_CONTEXT")
    if not context and environment.get("DOCKER_HOST"):
        return _endpoint_is_local(environment["DOCKER_HOST"])
    argv = ["docker", "context", "inspect"]
    if context:
        argv.append(context)
    argv.extend(["--format", "{{json .Endpoints.docker.Host}}"])
    try:
        result = _run(argv, project)
        if result.returncode:
            return None
        return _endpoint_is_local(json.loads(result.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _normalize_container(value: Any, expected_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("id") not in expected_ids:
        raise ValueError("unexpected container")
    name = value.get("name", "")
    if not isinstance(name, str):
        name = ""
    name = name.lstrip("/")
    if not _NAME.fullmatch(name):
        name = ""
    service = value.get("service")
    if not isinstance(service, str) or not _NAME.fullmatch(service):
        service = None
    state = value.get("state")
    if state not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}:
        state = "unknown"
    health = value.get("health")
    if health not in {"healthy", "unhealthy", "starting"}:
        health = None
    ports: list[dict[str, Any]] = []
    raw_ports = value.get("ports")
    if isinstance(raw_ports, dict):
        for target, bindings in raw_ports.items():
            if not isinstance(target, str) or not isinstance(bindings, list):
                continue
            match = re.fullmatch(r"([0-9]+)/([a-z]+)", target)
            if match is None or match.group(2) not in {"tcp", "udp", "sctp"}:
                continue
            target_port = int(match.group(1))
            if not 1 <= target_port <= 65535:
                continue
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                host = binding.get("HostIp")
                published = _integer(binding.get("HostPort"))
                try:
                    ipaddress.ip_address(host)
                except (ValueError, TypeError):
                    continue
                if published is not None and 1 <= published <= 65535:
                    ports.append({
                        "host_ip": host, "host_port": published,
                        "target_port": target_port, "protocol": match.group(2),
                    })
    return {
        "id": value["id"], "name": name, "service": service, "state": state,
        "health": health, "exit_code": _integer(value.get("exit_code")), "ports": ports,
    }


def collect_runtime(project: ComposeProject) -> RuntimeSnapshot:
    """Inspect this single Compose file without running any container commands."""
    try:
        profile_args = [argument for profile in project.active_profiles for argument in ("--profile", profile)]
        ps = _run([
            "docker", "compose", "--project-directory", str(project.base_dir),
            "-f", str(project.file), *profile_args, "ps", "--all", "--quiet",
        ], project)
        if ps.returncode:
            return _unavailable("Docker Compose could not read container state; check its installation, daemon access, permissions, and Compose configuration.")
        ids = list(dict.fromkeys(ps.stdout.split()))
        if len(ids) > _MAX_CONTAINERS:
            return _unavailable("The project exceeds the bounded runtime inspection limit of 128 containers.")
        if any(_HEX_ID.fullmatch(identifier) is None for identifier in ids):
            return _unavailable("Docker Compose returned an unsupported container ID response.")
        if not ids:
            return RuntimeSnapshot(True, checks=[Check("docker-runtime", "passed", "No containers were reported for this Compose project; application startup was not verified.")], local_daemon=_local_daemon(project))
        inspected = _run(["docker", "inspect", "--format", _INSPECT_FORMAT, *ids], project)
        if inspected.returncode:
            return _unavailable("Docker could not inspect the project containers; daemon access may have changed or a container may have disappeared.")
        values = [json.loads(line) for line in inspected.stdout.splitlines() if line.strip()]
        # IDs from ps may be abbreviated. Inspect returns their full forms.
        full_ids = {value.get("id") for value in values if isinstance(value, dict)}
        if len(values) != len(ids) or any(
            not isinstance(identifier, str) or _HEX_ID.fullmatch(identifier) is None
            or not any(identifier.startswith(short_id) for short_id in ids)
            for identifier in full_ids
        ) or len(full_ids) != len(ids) or any(
            not any(full_id.startswith(short_id) for full_id in full_ids) for short_id in ids
        ):
            return _unavailable("Docker returned an incomplete or unsupported inspection response.")
        containers = [_normalize_container(value, full_ids) for value in values]
        return RuntimeSnapshot(True, containers, checks=[Check("docker-runtime", "passed", "Read-only container state and published ports were collected; logs and container environment were not collected.")], local_daemon=_local_daemon(project))
    except FileNotFoundError:
        return _unavailable("The Docker CLI is not available on this host.")
    except subprocess.TimeoutExpired:
        return _unavailable("Docker runtime inspection timed out after 10 seconds per command.")
    except OSError:
        return _unavailable("The Docker CLI could not be executed on this host.")
    except (ValueError, TypeError, KeyError):
        return _unavailable("Docker returned an unsupported inspection response.")


def _dependencies(config: dict[str, Any]) -> list[tuple[str, str]]:
    dependencies = config.get("depends_on", {})
    if isinstance(dependencies, list):
        return [(name, "service_started") for name in dependencies if isinstance(name, str)]
    if not isinstance(dependencies, dict):
        return []
    result: list[tuple[str, str]] = []
    for name, options in dependencies.items():
        if not isinstance(name, str):
            continue
        options = options if isinstance(options, dict) else {}
        if options.get("required") is False:
            continue
        result.append((name, options.get("condition", "service_started")))
    return result


def diagnose_runtime(project: ComposeProject, snapshot: RuntimeSnapshot) -> list[Finding]:
    """Explain observed failed service states and unsatisfied required dependencies."""
    if not snapshot.available:
        return []
    findings: list[Finding] = []
    by_service: dict[str, list[dict[str, Any]]] = {}
    for container in snapshot.containers:
        by_service.setdefault(container.get("service"), []).append(container)
    for service, config in project.services.items():
        for dependency, condition in _dependencies(config):
            if dependency not in project.services:
                continue  # Static diagnostics cover missing/inactive dependencies.
            containers = by_service.get(dependency, [])
            if condition == "service_healthy":
                satisfied = bool(containers) and all(c.get("state") == "running" and c.get("health") == "healthy" for c in containers)
                expectation = "running and healthy"
            elif condition == "service_completed_successfully":
                satisfied = bool(containers) and all(c.get("state") == "exited" and c.get("exit_code") == 0 for c in containers)
                expectation = "completed with exit code 0"
            elif condition == "service_started":
                satisfied = bool(containers) and all(c.get("state") == "running" for c in containers)
                expectation = "running"
            else:
                continue
            if satisfied:
                continue
            location = project.location(service, "depends_on", dependency)
            observations = [
                Evidence(f"{dependency}: state={c.get('state', 'unknown')}, health={c.get('health') or 'none'}, exit_code={c.get('exit_code')}")
                for c in containers
            ] or [Evidence(f"Docker Compose reported no container for required service {dependency}.")]
            findings.append(Finding(
                "WNU007", "error", f"Required dependency {dependency} is not {expectation}",
                f"Service {service} requires {dependency} to be {expectation}. The current snapshot does not satisfy that condition. This is an observed dependency state, not proof of the underlying cause.",
                service=service, evidence=[Evidence(f"depends_on requires condition {condition}.", location.file, location.line), *observations],
                suggestion=Suggestion(f"Review {dependency}'s health check and startup configuration, and investigate its state before starting dependent services.", location.file, location.line),
            ))
        for container in by_service.get(service, []):
            state, exit_code = container.get("state"), container.get("exit_code")
            if state != "restarting" and not (state == "exited" and isinstance(exit_code, int) and exit_code != 0):
                continue
            location = project.location(service)
            findings.append(Finding(
                "WNU009", "error", f"Service {service} is {state}",
                "Docker reports a restarting container or a nonzero exit. Container state alone does not identify the root cause; review its configuration and investigate logs locally.",
                service=service, evidence=[Evidence(f"Container state={state}, exit_code={exit_code}.", location.file, location.line)],
                suggestion=Suggestion("Review the service command, dependencies, and configuration. Inspect logs locally before changing restart policies.", location.file, location.line),
            ))
    return findings


@dataclass(frozen=True)
class _PortRequest:
    service: str
    index: int
    host: str
    published: tuple[int, ...]
    targets: tuple[int, ...]
    protocol: str
    allocation_range: bool = False


def _range(value: Any) -> tuple[int, ...]:
    match = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", str(value))
    if match is None:
        raise ValueError("Published/target ports are not a numeric port or range.")
    first, last = int(match.group(1)), int(match.group(2) or match.group(1))
    if not 1 <= first <= last <= 65535:
        raise ValueError("The port value is outside the valid range.")
    if last - first + 1 > _MAX_RANGE:
        raise ValueError("Port ranges larger than 32 ports are not probed.")
    return tuple(range(first, last + 1))


def _port_request(service: str, index: int, value: Any) -> _PortRequest | str:
    host, protocol = "0.0.0.0", "tcp"
    if isinstance(value, dict):
        host = value.get("host_ip") or host
        protocol = value.get("protocol", "tcp")
        target, published = value.get("target"), value.get("published")
        if published is None or str(published) == "0":
            return "The host port is assigned dynamically; there is no fixed port to probe."
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value)
        if "/" in text:
            text, protocol = text.rsplit("/", 1)
        if text.startswith("["):
            match = re.fullmatch(r"\[([^]]+)\]:(.+)", text)
            if match is None:
                raise ValueError("The IPv6 host binding must use bracket notation.")
            host, text = match.groups()
            parts = text.split(":")
            if len(parts) != 2:
                raise ValueError("Unsupported published port syntax.")
        else:
            parts = text.split(":")
            if len(parts) == 3:
                host, *parts = parts
            elif len(parts) == 1:
                return "The host port is assigned dynamically; there is no fixed port to probe."
            elif len(parts) != 2:
                raise ValueError("Unsupported published port syntax.")
        published, target = parts
        if published == "0":
            return "The host port is assigned dynamically; there is no fixed port to probe."
    else:
        raise ValueError("Unsupported published port syntax.")
    if protocol not in {"tcp", "udp", "sctp"}:
        raise ValueError("Unsupported port protocol.")
    if not isinstance(host, str):
        raise ValueError("The host binding is not an IP address.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("The host binding is not an IP address.") from None
    if protocol != "tcp":
        return f"{protocol.upper()} bindings are not tested; TCP bind checks cannot verify them."
    ports, targets = _range(published), _range(target)
    allocation_range = len(ports) > 1 and len(targets) == 1
    if len(ports) != len(targets) and not allocation_range:
        raise ValueError("The published and target ranges have incompatible lengths.")
    return _PortRequest(service, index, host, ports, targets, protocol, allocation_range)


def _already_owned(request: _PortRequest, published: int, target: int, snapshot: RuntimeSnapshot | None) -> bool:
    if snapshot is None or not snapshot.available or snapshot.local_daemon is not True:
        return False
    return any(
        container.get("service") == request.service and container.get("state") == "running"
        and any(
            port.get("host_port") == published and port.get("target_port") == target
            and port.get("protocol") == request.protocol and port.get("host_ip") == request.host
            for port in container.get("ports", [])
        )
        for container in snapshot.containers
    )


def _probe(host: str, port: int) -> str:
    family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind((host, port))
        return "passed"
    except OSError as error:
        return "failed" if error.errno == errno.EADDRINUSE else "unavailable"


def _occupied(project: ComposeProject, request: _PortRequest, ports: tuple[int, ...]) -> Finding:
    location = project.location(request.service, "ports", request.index)
    display = str(ports[0]) if len(ports) == 1 else f"{ports[0]}-{ports[-1]}"
    return Finding(
        "WNU004", "error", f"TCP host port {display} is already in use",
        f"A temporary TCP bind on {request.host}:{display} failed because the address is in use. This may prevent the configured publication. The existing owner was not verified and could be a container from this stack whose mapping could not be matched.",
        service=request.service, confidence="probable",
        evidence=[Evidence(f"Published TCP binding {request.host}:{display}; bind returned address already in use.", location.file, location.line)],
        suggestion=Suggestion("Identify the existing owner before changing anything. Stop it only if appropriate, or select a different available host port in this mapping.", location.file, location.line),
    )


def check_ports(project: ComposeProject, snapshot: RuntimeSnapshot | None = None) -> tuple[list[Finding], list[Check]]:
    """Probe fixed local TCP publications; never stop a process or reserve a port."""
    findings: list[Finding] = []
    checks: list[Check] = []
    probes = 0
    for service, config in project.services.items():
        if config.get("network_mode") == "host":
            if config.get("ports"):
                checks.append(Check(f"ports:{service}", "skipped", "Host-network services do not use published port mappings; their listening sockets were not verified."))
            continue
        mappings = config.get("ports", [])
        if not isinstance(mappings, list):
            checks.append(Check(f"ports:{service}", "unavailable", "Ports must be a list to probe their host bindings."))
            continue
        for index, mapping in enumerate(mappings):
            name = f"ports:{service}:{index}"
            try:
                request = _port_request(service, index, mapping)
            except ValueError as error:
                checks.append(Check(name, "unavailable", str(error)))
                continue
            if isinstance(request, str):
                checks.append(Check(name, "skipped", request))
                continue
            if request.protocol != "tcp":
                checks.append(Check(name, "skipped", f"{request.protocol.upper()} bindings are not tested; TCP bind checks cannot verify them."))
                continue
            if snapshot is not None and snapshot.local_daemon is not True:
                scope = "remote" if snapshot.local_daemon is False else "unknown"
                checks.append(Check(name, "unavailable", f"The selected Docker endpoint is {scope}. Local TCP bind probes cannot verify ports on that daemon's host."))
                continue
            if request.allocation_range:
                if any(_already_owned(request, port, request.targets[0], snapshot) for port in request.published):
                    checks.append(Check(name, "skipped", "A running container in this project already owns a matching publication from this allocation range."))
                    continue
                results: list[str] = []
                for port in request.published:
                    if probes >= _MAX_PORT_PROBES:
                        results.append("unavailable")
                        break
                    probes += 1
                    result = _probe(request.host, port)
                    results.append(result)
                    if result == "passed":
                        break
                if "passed" in results:
                    checks.append(Check(name, "passed", "At least one TCP host port in the publication allocation range could be bound; availability may change."))
                elif "unavailable" in results:
                    checks.append(Check(name, "unavailable", "The full allocation range could not be checked on this host or within the 128-probe limit."))
                else:
                    checks.append(Check(name, "failed", "Every TCP port in the allocation range is already in use; ownership was not verified."))
                    findings.append(_occupied(project, request, request.published))
                continue
            for offset, (published, target) in enumerate(zip(request.published, request.targets)):
                item_name = name if len(request.published) == 1 else f"{name}:{offset}"
                if _already_owned(request, published, target, snapshot):
                    checks.append(Check(item_name, "skipped", "A running container in this project already owns this matching publication."))
                    continue
                if probes >= _MAX_PORT_PROBES:
                    checks.append(Check(item_name, "unavailable", "The bounded limit of 128 TCP bind probes was reached."))
                    continue
                probes += 1
                result = _probe(request.host, published)
                reason = {
                    "passed": "The TCP binding could be opened temporarily on this host; availability may change.",
                    "failed": "The TCP address is already in use; the existing owner was not verified.",
                    "unavailable": "This host binding could not be probed (for example, unavailable interface, permissions, or address family).",
                }[result]
                checks.append(Check(item_name, result, reason))
                if result == "failed":
                    findings.append(_occupied(project, request, (published,)))
    if not checks:
        checks.append(Check("ports", "skipped", "No fixed published host ports were configured."))
    return findings, checks
