"""Opt-in, bounded log-pattern checks without retaining log text in reports.

Historical messages are clues for review, not evidence of a current root cause.
Only callers that explicitly requested log collection should invoke this module.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO

from .models import Check, ComposeProject, Evidence, Finding, RuntimeSnapshot, Suggestion


_MAX_CONTAINERS = 16
_MAX_BYTES = 64 * 1024
_TIMEOUT = 8
_ID = re.compile(r"[a-fA-F0-9]{12,64}\Z")


@dataclass(frozen=True)
class _Pattern:
    code: str
    title: str
    expression: re.Pattern[str]
    suggestion: str


_PATTERNS = (
    _Pattern("WNU201", "Connection-refused messages in recent logs",
             re.compile(r"\b(?:ECONNREFUSED|connection\s+(?:was\s+)?refused)\b", re.I),
             "Review the intended connection host and port, the destination's readiness, and whether it listens on that interface."),
    _Pattern("WNU202", "DNS-resolution messages in recent logs",
             re.compile(r"\b(?:ENOTFOUND|EAI_AGAIN|UnknownHostException|temporary failure in name resolution|"
                        r"name or service not known|no such host|(?:DNS|name|hostname) resolution (?:failed|failure|error)|"
                        r"(?:could not|cannot|unable to) resolve (?:host|hostname|service|name))\b", re.I),
             "Review the intended hostname, shared Compose networks, and resolver configuration; verify whether a retry later succeeded."),
    _Pattern("WNU203", "Authentication-denied messages in recent logs",
             re.compile(r"\b(?:authentication\s+(?:failed|failure|denied|required)|password authentication failed|"
                        r"access denied for user|invalid (?:username[- /]password|credentials)|WRONGPASS|"
                        r"NOAUTH|unauthorized\s*:\s*authentication required)\b", re.I),
             "Review the application's credential source and the destination's authentication settings. Do not publish credentials or raw logs."),
    _Pattern("WNU204", "Permission-denied messages in recent logs",
             re.compile(r"\b(?:EACCES|EPERM|permission denied|operation not permitted)\b", re.I),
             "Review the required file or operation and the container user, mount mode, and access ownership. Grant only the access that is needed."),
    _Pattern("WNU205", "Out-of-memory messages in recent logs",
             re.compile(r"\b(?:out[ -]of[ -]memory|OOMKilled|oom[ -]kill(?:er|ed)?)\b", re.I),
             "Review memory usage, container limits, and observed container state before deciding whether memory pressure caused a failure."),
    _Pattern("WNU206", "Address-in-use messages in recent logs",
             re.compile(r"\b(?:EADDRINUSE|address (?:already )?in use|port is already allocated)\b", re.I),
             "Review the listener and its intended binding, then identify any existing owner before changing ports or stopping services."),
)


@dataclass(frozen=True)
class _LogRead:
    data: bytes = b""
    unavailable: bool = False
    truncated: bool = False
    reason: str = ""


def _stop(process: subprocess.Popen) -> None:
    """Bound cleanup too, without echoing potentially sensitive CLI errors."""
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _read_logs(project: ComposeProject, identifier: str, tail: int) -> _LogRead:
    # stdout and stderr both contain application logs with Docker's CLI. Keeping
    # them in one pipe avoids deadlock, and no communicate()/unbounded capture is
    # used. The pipe and this bytearray have fixed bounds, even for a huge line.
    try:
        process = subprocess.Popen(
            ["docker", "logs", "--tail", str(tail), identifier],
            cwd=project.base_dir, env={**os.environ, **project.environment},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            shell=False,
        )
    except (OSError, ValueError):
        return _LogRead(unavailable=True, reason="The Docker CLI could not read container logs.")
    if process.stdout is None:
        _stop(process)
        return _LogRead(unavailable=True, reason="The Docker log output pipe was unavailable.")

    stream: BinaryIO = process.stdout
    buffer = bytearray()
    done = threading.Event()
    read_failed = []

    def consume() -> None:
        try:
            while len(buffer) <= _MAX_BYTES:
                block = stream.read(min(4096, _MAX_BYTES + 1 - len(buffer)))
                if not block:
                    break
                buffer.extend(block)
        except (OSError, ValueError):
            read_failed.append(True)
        finally:
            done.set()
            try:
                stream.close()
            except OSError:
                pass

    deadline = time.monotonic() + _TIMEOUT
    reader = threading.Thread(target=consume, name="whynotup-log-reader", daemon=True)
    reader.start()
    if not done.wait(_TIMEOUT):
        _stop(process)
        reader.join(timeout=1)
        return _LogRead(unavailable=True, reason="Docker log collection timed out; this container's log check is incomplete.")
    if len(buffer) > _MAX_BYTES:
        _stop(process)
        reader.join(timeout=1)
        return _LogRead(bytes(buffer[:_MAX_BYTES]), unavailable=True, truncated=True,
                        reason="Docker log output exceeded the 64 KiB per-container limit; only a bounded excerpt was checked.")
    try:
        result = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        _stop(process)
        return _LogRead(unavailable=True, reason="Docker log collection timed out or could not complete.")
    if read_failed or result:
        # Failed CLI output can describe a daemon error rather than an app error.
        return _LogRead(unavailable=True, reason="Docker could not collect this container's logs; the container may have disappeared or its logging driver may not support reads.")
    return _LogRead(bytes(buffer))


def _is_negated(line: str, match: re.Match[str]) -> bool:
    before = line[max(0, match.start() - 48):match.start()]
    after = line[match.end():match.end() + 48]
    # Basic scoped negation avoids turning status counters/documentation-like
    # lines into errors, without suppressing an unrelated error later in a line.
    if re.search(r"\b(?:no|not|never|without|zero|0)\s+(?:(?:\w+[- ]?){0,3})$", before, re.I):
        return True
    if re.match(r"\s*(?:errors?|failures?|events?|occurrences?|count)?\s*[:=]\s*0\b", after, re.I):
        return True
    if re.match(r"\s*[:=]\s*(?:false|none|no)\b", after, re.I):
        return True
    return False


def _counts(data: bytes) -> dict[str, int]:
    counts = {pattern.code: 0 for pattern in _PATTERNS}
    for line in data.decode("utf-8", errors="replace").splitlines():
        for pattern in _PATTERNS:
            # Count matching lines, not repeated phrases within one message.
            if any(not _is_negated(line, match) for match in pattern.expression.finditer(line)):
                counts[pattern.code] += 1
    return counts


def collect_log_findings(project: ComposeProject, snapshot: RuntimeSnapshot, *,
                         tail: int = 100) -> tuple[list[Finding], list[Check]]:
    """Read selected snapshot containers only; never include raw logs in output.

    The caller controls opt-in. An unavailable snapshot cannot authorize reads.
    Even successful collection is limited to the requested historical tail.
    """
    if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 1000:
        return [], [Check("docker-logs", "unavailable", "Log tail must be an integer between 1 and 1000.")]
    if not snapshot.available:
        return [], [Check("docker-logs", "unavailable", "A verified runtime snapshot is required before container logs can be read.")]

    selected = []
    seen = set()
    invalid = 0
    for container in snapshot.containers:
        if not isinstance(container, dict):
            invalid += 1
            continue
        service, identifier = container.get("service"), container.get("id")
        if not isinstance(service, str) or service not in project.services:
            continue
        if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
            invalid += 1
            continue
        if identifier not in seen:
            seen.add(identifier)
            selected.append((service, identifier))

    totals: dict[tuple[str, str], int] = {}
    failed = truncated = completed = 0
    read_reasons = set()
    for service, identifier in selected[:_MAX_CONTAINERS]:
        result = _read_logs(project, identifier, tail)
        failed += int(result.unavailable)
        truncated += int(result.truncated)
        completed += int(not result.unavailable)
        if result.reason:
            read_reasons.add(result.reason)
        if result.data:
            for code, count in _counts(result.data).items():
                if count:
                    totals[(service, code)] = totals.get((service, code), 0) + count

    excess = max(0, len(selected) - _MAX_CONTAINERS)
    incomplete = bool(failed or invalid or excess)
    status = "unavailable" if incomplete else "completed"
    reason = (f"Requested the last {tail} log lines per container. "
              f"Completed {completed} of {min(len(selected), _MAX_CONTAINERS)} attempted container reads; "
              f"{failed} incomplete, {truncated} truncated at 64 KiB, "
              f"{excess} skipped above the 16-container limit, {invalid} invalid snapshot entries. "
              "Only pattern families and matching-line counts are reported; raw logs are omitted. "
              "Historical messages do not prove a current failure.")
    if read_reasons:
        reason += " " + " ".join(sorted(read_reasons))
    if not selected and not invalid:
        reason += " No containers belonging to active services were available to inspect."
    findings = []
    for (service, code), count in sorted(totals.items()):
        pattern = next(item for item in _PATTERNS if item.code == code)
        findings.append(Finding(
            code=code, severity="warning", title=pattern.title,
            explanation=(f"This pattern matched {count} line(s) in bounded output requested from the last {tail} log lines per container. "
                         "Messages may be historical, recovered, or emitted by a dependency; they do not establish a current failure or its root cause. "
                         "Review the surrounding context locally before acting."),
            service=service, confidence="possible",
            evidence=[Evidence(f"{code} pattern family: {count} matching line(s); requested tail={tail}; raw log content omitted.")],
            suggestion=Suggestion(pattern.suggestion),
        ))
    return findings, [Check("docker-logs", status, reason)]
