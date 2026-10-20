from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int | None = None


@dataclass(frozen=True)
class Evidence:
    summary: str
    file: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class Suggestion:
    description: str
    file: str | None = None
    line: int | None = None
    before: str | None = None
    after: str | None = None


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    title: str
    explanation: str
    service: str | None = None
    confidence: str = "confirmed"
    evidence: list[Evidence] = field(default_factory=list)
    suggestion: Suggestion | None = None


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str | None = None


@dataclass
class ComposeProject:
    base_dir: Path
    file: Path
    services: dict[str, dict[str, Any]]
    raw_services: dict[str, dict[str, Any]]
    environment: dict[str, str]
    service_environment: dict[str, dict[str, str]]
    locations: dict[tuple[Any, ...], SourceLocation]
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    secrets: set[str] = field(default_factory=set)
    active_profiles: tuple[str, ...] = ()
    interpolation_env_file: Path | None = None

    def location(self, service: str, *path: Any) -> SourceLocation:
        key = ("services", service, *path)
        while key:
            if key in self.locations:
                return self.locations[key]
            key = key[:-1]
        return SourceLocation(self.file.name)


@dataclass
class RuntimeSnapshot:
    available: bool
    containers: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    checks: list[Check] = field(default_factory=list)
    local_daemon: bool | None = None


@dataclass
class Report:
    project: str
    compose_file: str
    findings: list[Finding]
    checks: list[Check]
    notes: list[str] = field(default_factory=list)
    schema_version: str = "1.0"
