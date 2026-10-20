"""Deterministic configuration advice, separate from startup diagnosis.

These checks inspect declarations only. They do not inspect image metadata,
application behavior, host firewall rules, or deployment-provider defaults.
"""

from __future__ import annotations

import ipaddress
import math
import posixpath
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .models import ComposeProject, Evidence, Finding, Suggestion


_SENSITIVE_NAME = re.compile(
    r"(?:^|_)(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY|CREDENTIALS?|AUTHORIZATION)(?:$|_)",
    re.I,
)
_SETTING_SUFFIXES = ("_FILE", "_PATH", "_DIR", "_URL", "_URI", "_ENDPOINT", "_ENABLED", "_REQUIRED", "_LENGTH", "_TTL", "_TIMEOUT", "_NAME")
_REFERENCE = re.compile(r"^(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*(?:(?::?\?)[^}]*|(?::-|-))?\})$")
_MOUNT_MODES = {"ro", "rw", "z", "Z", "cached", "delegated", "consistent", "nocopy"}
_DATABASE_DATA = {"postgres": "/var/lib/postgresql/data", "postgresql": "/var/lib/postgresql/data", "mysql": "/var/lib/mysql", "mariadb": "/var/lib/mysql", "mongo": "/data/db", "mongodb": "/data/db", "redis": "/data"}
_DATABASE_CLIENTS = {"psql", "pg_dump", "pg_dumpall", "pg_restore", "mysql", "mysqldump", "mariadb", "mariadb-dump", "mongosh", "mongo", "mongodump", "mongorestore", "redis-cli"}


@dataclass(frozen=True)
class _Mount:
    source: str | None
    target: str
    kind: str
    read_only: bool
    index: int


def review_project(project: ComposeProject, *, target: str = "production") -> list[Finding]:
    """Return review advice for active services without modifying the project."""
    if target not in ("production", "development"):
        raise ValueError("Review target must be production or development.")
    jobs = _known_jobs(project)
    findings = []
    for name, service in project.services.items():
        checks = [
            _image(project, name, service),
            _privileged(project, name, service),
            _root_user(project, name, service),
            _publications(project, name, service),
            _docker_socket(project, name, service),
            _inline_secrets(project, name),
        ]
        findings.extend(finding for finding in checks if finding is not None)
        findings.extend(_readiness(project, name, service, jobs))
        if target == "production":
            for finding in (_resources(project, name, service), _restart(project, name, service, jobs)):
                if finding is not None:
                    findings.append(finding)
        finding = _host_network(project, name, service)
        if finding is not None:
            findings.append(finding)
        if target == "production":
            finding = _persistence(project, name, service, jobs)
            if finding is not None:
                findings.append(finding)
    return findings


def _finding(project, name, code, title, explanation, summary, suggestion, *path,
             confidence="confirmed", evidence=None):
    location = project.location(name, *path)
    return Finding(
        code=code, severity="warning", title=title, explanation=explanation,
        service=name, confidence=confidence,
        evidence=evidence if evidence is not None else [Evidence(summary, location.file, location.line)],
        suggestion=Suggestion(suggestion, location.file, location.line),
    )


def _true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower().strip() == "true")


def _image(project, name, service):
    image = service.get("image")
    if not isinstance(image, str) or not image or "${" in image:
        return None
    if "@" in image and image.rsplit("@", 1)[1]:
        return None
    basename = image.rsplit("/", 1)[-1]
    tag = basename.rsplit(":", 1)[1] if ":" in basename else ""
    if tag and tag != "latest":
        return None
    return _finding(project, name, "WNU101", "Image uses a floating latest reference",
        "The image has no explicit tag or uses latest. Pulling it at a different time may select different contents. A version tag can also move; a digest identifies fixed contents.",
        "The image reference has no explicit version tag or declares latest.",
        "Select a tested release tag, or a verified image digest when immutable deployment is required. No release is guessed.", "image")


def _privileged(project, name, service):
    if not _true(service.get("privileged")):
        return None
    return _finding(project, name, "WNU102", "Privileged container mode is enabled",
        "Privileged mode grants broad capabilities and host-device access. This can substantially weaken container isolation; whether the workload requires it has not been verified.",
        "privileged is explicitly enabled.",
        "Confirm that privileged mode is required. Where possible, replace it with the specific capabilities and devices the workload needs.", "privileged")


def _root_user(project, name, service):
    user = service.get("user")
    if isinstance(user, bool) or not isinstance(user, (str, int)):
        return None
    username = str(user).split(":", 1)[0]
    if username != "root" and re.fullmatch(r"0+", username) is None:
        return None
    return _finding(project, name, "WNU103", "Container user is explicitly root",
        "The service explicitly requests root or UID 0 inside its container. The host identity still depends on user namespaces and the runtime. An omitted user is not assumed to be root.",
        "user explicitly selects root or UID 0.",
        "Check the image's supported non-root user and required file permissions before changing user. No UID is invented.", "user")


def _uses_host_namespace(project, name):
    visited = set()
    while name not in visited:
        visited.add(name)
        mode = project.services.get(name, {}).get("network_mode")
        if mode == "host":
            return True
        if isinstance(mode, str) and mode.startswith("service:"):
            name = mode.split(":", 1)[1]
        else:
            return False
    return False


def _publications(project, name, service):
    ports = service.get("ports", [])
    if not isinstance(ports, list) or _uses_host_namespace(project, name):
        return None
    evidence = []
    for index, publication in enumerate(ports):
        address = None
        if isinstance(publication, dict):
            if publication.get("target") is None:
                continue
            address = publication.get("host_ip")
            path = ("ports", index, "host_ip") if address else ("ports", index)
        elif isinstance(publication, (str, int)) and not isinstance(publication, bool):
            value = str(publication).split("/", 1)[0]
            if not value:
                continue
            if value.startswith("[") and "]:" in value:
                address = value[1:value.index("]")]
            elif value.count(":") >= 2:
                address = value.rsplit(":", 2)[0]
            path = ("ports", index)
        else:
            continue
        if address:
            try:
                if not ipaddress.ip_address(str(address).strip("[]")).is_unspecified:
                    continue
            except ValueError:
                continue  # an unrecognized address needs parser/runtime context
        location = project.location(name, *path)
        summary = "Port publication has no explicit host interface." if not address else "Port publication explicitly selects an unspecified host address (all interfaces)."
        evidence.append(Evidence(summary, location.file, location.line))
    if not evidence:
        return None
    return _finding(project, name, "WNU104", "Published ports may listen on broad host interfaces",
        "These publications omit a host interface or select an unspecified address. Docker commonly binds these on all interfaces, subject to network and daemon defaults. Firewall rules and actual external reachability were not inspected.",
        "", "If the ports should be local only, bind them to an appropriate loopback address. Keep intentionally public endpoints scoped to their intended network.",
        "ports", confidence="probable", evidence=evidence)


def _mounts(service):
    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        return
    for index, volume in enumerate(volumes):
        if isinstance(volume, dict):
            source, target = volume.get("source"), volume.get("target")
            if isinstance(target, str) and isinstance(volume.get("type", "volume"), str):
                yield _Mount(source if isinstance(source, str) else None, target, volume.get("type", "volume"), _true(volume.get("read_only")), index)
        elif isinstance(volume, str):
            drive = bool(re.match(r"^[A-Za-z]:[\\/]", volume))
            parts = volume[2:].split(":") if drive else volume.split(":")
            if drive:
                parts[0] = volume[:2] + parts[0]
            if len(parts) == 1 or (len(parts) == 2 and all(mode in _MOUNT_MODES for mode in parts[1].split(","))):
                yield _Mount(None, parts[0], "volume", len(parts) == 2 and "ro" in parts[1].split(","), index)
                continue
            source, target = parts[:2]
            kind = "bind" if source.startswith((".", "/", "~")) or "/" in source or "\\" in source else "volume"
            yield _Mount(source, target, kind, len(parts) > 2 and "ro" in parts[2].split(","), index)


def _docker_socket(project, name, service):
    evidence = []
    for mount in _mounts(service):
        if mount.kind != "bind" or not mount.source:
            continue
        source = mount.source.replace("\\", "/").rstrip("/")
        if source.rsplit("/", 1)[-1] != "docker.sock":
            continue
        location = project.location(name, "volumes", mount.index)
        evidence.append(Evidence("A host path named docker.sock is bind-mounted" + (" with read_only enabled." if mount.read_only else "."), location.file, location.line))
    if not evidence:
        return None
    return _finding(project, name, "WNU105", "Docker daemon socket is exposed to a container",
        "A bind source named docker.sock is commonly the Docker daemon socket. If it is the daemon socket, API access may allow control of that daemon's containers and host resources. A read-only filesystem mount does not make the socket API read-only; socket identity and permissions were not inspected.",
        "", "Remove the socket bind when it is unnecessary, or provide a deliberately restricted API/proxy suited to the required operations.",
        "volumes", confidence="probable", evidence=evidence)


def _raw_environment(project, name):
    raw = project.raw_services.get(name, {}).get("environment", {})
    if isinstance(raw, dict):
        return {key: (value, ("environment", key)) for key, value in raw.items() if isinstance(key, str)}
    entries = {}
    if isinstance(raw, list):
        for index, entry in enumerate(raw):
            if isinstance(entry, str):
                key, separator, value = entry.partition("=")
                entries[key] = (value if separator else None, ("environment", index))
    return entries


def _literal(value):
    return value is not None and str(value) != "" and _REFERENCE.fullmatch(str(value)) is None


def _inline_secrets(project, name):
    evidence = []
    for key, (value, path) in _raw_environment(project, name).items():
        if not _literal(value):
            continue
        sensitive_name = _SENSITIVE_NAME.search(key) and not key.upper().endswith(_SETTING_SUFFIXES)
        url_password = None
        if isinstance(value, str):
            try:
                url_password = urlsplit(value).password
            except ValueError:
                pass
        if not sensitive_name and not _literal(url_password):
            continue
        location = project.location(name, *path)
        evidence.append(Evidence(f"{key} contains an inline literal in environment; its value is intentionally omitted.", location.file, location.line))
    if not evidence:
        return None
    return _finding(project, name, "WNU106", "Potential secret is declared inline in environment",
        "A sensitive-looking environment setting, or a URL with literal credentials, is declared directly in the Compose file. This can expose credentials when the file is shared or committed. Classification uses setting names and URL structure; it does not prove that the value is a live secret.",
        "", "Supply real credentials through runtime configuration or an appropriate secrets mechanism. Keep secret values out of committed Compose files and rotate any exposed live credential. No value or replacement is included in this report.",
        "environment", confidence="probable", evidence=evidence)


def _known_jobs(project):
    jobs = set()
    for name, service in project.services.items():
        deploy = service.get("deploy") or {}
        if service.get("restart") == "no" or (isinstance(deploy, dict) and deploy.get("mode") in ("replicated-job", "global-job")):
            jobs.add(name)
        dependencies = service.get("depends_on", {})
        if isinstance(dependencies, dict):
            for dependency, settings in dependencies.items():
                if isinstance(settings, dict) and settings.get("condition") == "service_completed_successfully":
                    jobs.add(dependency)
    return jobs


def _readiness(project, name, service, jobs):
    dependencies = service.get("depends_on", {})
    if isinstance(dependencies, list):
        entries = [(dependency, {}, ("depends_on", index)) for index, dependency in enumerate(dependencies)]
    elif isinstance(dependencies, dict):
        entries = [(dependency, settings or {}, ("depends_on", dependency)) for dependency, settings in dependencies.items()]
    else:
        return []
    findings = []
    for dependency, settings, path in entries:
        if (not isinstance(dependency, str) or dependency not in project.services or dependency in jobs or not isinstance(settings, dict)
                or settings.get("required") is False or settings.get("condition", "service_started") != "service_started"):
            continue
        healthcheck = project.services[dependency].get("healthcheck")
        has_healthcheck = isinstance(healthcheck, dict) and not _true(healthcheck.get("disable")) and healthcheck.get("test") not in (None, "NONE", ["NONE"])
        suggestion = ("If startup requires this dependency to be ready, verify its existing healthcheck and use condition: service_healthy. Applications should still retry transient failures."
                      if has_healthcheck else
                      "If startup requires this dependency to be ready, define a workload-specific healthcheck and use condition: service_healthy. An image may already provide a healthcheck; inspect it before adding one. Applications should still retry transient failures.")
        findings.append(_finding(project, name, "WNU107", "Dependency waits for startup rather than readiness",
            f"The dependency on {dependency!r} uses service_started, explicitly or through short syntax. This orders container startup but does not wait for a healthy application, even when a healthcheck is defined. Whether the application already retries connections was not inspected.",
            f"Required dependency {dependency!r} uses the service_started condition.", suggestion, *path))
    return findings


def _positive(value):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number > 0


def _memory_limit(value):
    if _positive(value):
        return True
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[bkmgtpe](?:i?b)?\s*", value, re.I)
        return bool(match and _positive(match.group(1)))
    return False


def _resources(project, name, service):
    deploy = service.get("deploy") or {}
    resources = deploy.get("resources", {}) if isinstance(deploy, dict) else {}
    limits = resources.get("limits", {}) if isinstance(resources, dict) else {}
    limits = limits if isinstance(limits, dict) else {}
    cpu = _positive(service.get("cpus")) or _positive(service.get("cpu_quota")) or _positive(limits.get("cpus"))
    memory = _memory_limit(service.get("mem_limit")) or _memory_limit(limits.get("memory"))
    missing = [label for label, present in (("CPU", cpu), ("memory", memory)) if not present]
    if not missing:
        return None
    label = " and ".join(missing)
    return _finding(project, name, "WNU108", "Production resource limits are not fully declared",
        f"No positive {label} limit is declared for this service. Reservations and CPU shares do not impose these caps. Runtime or platform defaults were not inspected, and enforcement depends on the deployment provider.",
        f"Missing declared limits: {label}.",
        "Measure the workload and choose appropriate CPU and memory limits using cpus/mem_limit or deploy.resources.limits. No capacity values are guessed.", "deploy", "resources")


def _restart(project, name, service, jobs):
    deploy = service.get("deploy") or {}
    if (name in jobs or (isinstance(service.get("restart"), str) and service["restart"])
            or (isinstance(deploy, dict) and isinstance(deploy.get("restart_policy"), dict))):
        return None
    return _finding(project, name, "WNU109", "Production service has no declared restart policy",
        "No restart or deploy.restart_policy is declared for this apparently long-running service. Automatic recovery and any platform defaults were not inspected. Jobs referenced with service_completed_successfully and explicit restart: no are excluded.",
        "Neither restart nor deploy.restart_policy is declared.",
        "Choose a restart policy appropriate to the service and deployment provider, or explicitly use restart: no for an intentionally unmanaged or one-shot workload.")


def _host_network(project, name, service):
    if service.get("network_mode") != "host":
        return None
    return _finding(project, name, "WNU110", "Host network mode is enabled",
        "The service declares host networking. On platforms where it is supported, this shares the host network namespace and removes the usual separate container network boundary. Published port mappings do not restrict that access.",
        "network_mode is explicitly host.",
        "Confirm that host networking is required. Otherwise use a suitable Compose network and explicitly scoped publications; check platform support before changing it.", "network_mode")


def _persistence(project, name, service, jobs):
    image = service.get("image")
    if name in jobs or not isinstance(image, str) or service.get("volumes_from"):
        return None
    image_name = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
    data_path = _DATABASE_DATA.get(image_name)
    if data_path is None:
        return None
    command = service.get("command")
    executable = command[0] if isinstance(command, list) and command else command.split()[0] if isinstance(command, str) and command.strip() else ""
    if isinstance(executable, str) and executable.rsplit("/", 1)[-1] in _DATABASE_CLIENTS:
        return None
    if image_name in {"postgres", "postgresql"}:
        pgdata = project.service_environment.get(name, {}).get("PGDATA")
        if isinstance(pgdata, str) and pgdata.startswith("/") and "${" not in pgdata:
            data_path = pgdata
        else:
            tag = image.split("@", 1)[0].rsplit("/", 1)[-1].partition(":")[2]
            major = re.match(r"(\d+)", tag)
            if major and (len(major.group(1)) > 3 or int(major.group(1)) >= 18):
                data_path = "/var/lib/postgresql"
    for mount in _mounts(service):
        if mount.kind not in {"volume", "bind"} or mount.read_only or not mount.target.startswith("/"):
            continue
        target = posixpath.normpath(mount.target)
        if data_path == target or data_path.startswith(target.rstrip("/") + "/"):
            return None
    return _finding(project, name, "WNU111", "Database data has no declared persistent mount",
        f"The image name resembles a database whose usual data location is {data_path!r}, but no writable bind or volume mount covers that location. Image defaults, command-line overrides, and whether this database is intentionally disposable were not verified. An image-created anonymous volume is not an explicitly managed persistence plan.",
        f"No writable declared bind or volume covers the candidate data path {data_path!r}.",
        "Confirm the image's actual data directory and recovery requirements. If data must survive replacement, declare an appropriate volume or bind mount and a separate backup strategy. Preserve intentionally disposable databases when that is the intended design.",
        "volumes", confidence="probable")
