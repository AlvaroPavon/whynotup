"""Opt-in, daemon-independent validation by the installed Compose implementation."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass

from .models import Check, ComposeProject, Evidence, Finding, Suggestion
from .redaction import Redactor
from .runtime import _effective_environment


_CHECK = "docker-compose-validation"
_MAX_STDERR = 16384
_MAX_EVIDENCE = 4
_TIMEOUT = 10
_UNAVAILABLE = re.compile(
    r"unknown (?:shorthand )?flag:|flag provided but not defined:|unknown option(?:\s|:)"
    r"|(?:['\"]?compose['\"]? is not a docker command)"
    r"|unknown command:?\s+['\"]?(?:docker\s+)?compose\b"
    r"|cannot connect to the docker daemon|error during connect"
    r"|permission denied while trying to connect|unable to resolve docker (?:endpoint|context)"
    r"|context[^\n]*(?:not found|does not exist)"
    r"|failed to (?:fetch|load|execute)[^\n]*cli plugin"
    r"|(?:^|\n)(?:panic:|fatal error:)", re.I,
)
_PATH = re.compile(r"\bservices(?:\.[A-Za-z_][A-Za-z0-9_-]{0,63}){1,5}\b")
_REASONS = (
    (r"additional propert(?:y|ies)|unsupported config(?:uration)? (?:option|propert|field)", "unsupported configuration property"),
    (r"must be (?:a |an )?(?:string|mapping|boolean|integer|number|array|object)|invalid type", "configuration value has an invalid type"),
    (r"must be unique|duplicate", "configuration contains duplicate entries"),
    (r"undefined (?:network|volume|service|secret|config)", "configuration refers to an undefined resource"),
    (r"required variable|variable[^\n]*(?:required|missing)", "required interpolation variable is unset"),
    (r"env(?:ironment)? file[^\n]*(?:not found|does not exist|no such file)", "required environment file is missing"),
    (r"invalid[^\n]*(?:port|published)|port[^\n]*invalid", "invalid port mapping"),
    (r"invalid[^\n]*mount|mount[^\n]*invalid", "invalid mount configuration"),
    (r"invalid[^\n]*restart|restart[^\n]*invalid", "invalid restart policy"),
    (r"yaml:|unmarshal|cannot unmarshal", "Compose could not parse a configuration input"),
    (r"invalid compose project", "Compose project semantics are invalid"),
)


def _unavailable(reason: str) -> tuple[list[Finding], list[Check]]:
    return [], [Check(_CHECK, "unavailable", reason)]


@dataclass(frozen=True)
class _ValidationRead:
    returncode: int | None = None
    stderr: bytes = b""
    reason: str | None = None


def _stop(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _capture_validation(argv: list[str], project: ComposeProject) -> _ValidationRead:
    """Bound stderr before retaining it, including one byte to detect overflow."""
    deadline = time.monotonic() + _TIMEOUT
    try:
        process = subprocess.Popen(
            argv, cwd=project.base_dir, env=_effective_environment(project),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, shell=False, bufsize=0,
        )
    except FileNotFoundError:
        return _ValidationRead(reason="The Docker CLI is not installed or could not be found.")
    except (OSError, ValueError):
        return _ValidationRead(reason="Docker Compose validation could not be executed on this host.")
    if process.stderr is None:
        _stop(process)
        return _ValidationRead(reason="Docker Compose validation did not provide its diagnostic output pipe.")
    stream = process.stderr
    buffer = bytearray()
    done = threading.Event()
    read_failed = []

    def consume() -> None:
        try:
            while len(buffer) <= _MAX_STDERR:
                remaining = _MAX_STDERR + 1 - len(buffer)
                block = stream.read(min(4096, remaining))
                if not block:
                    break
                buffer.extend(block[:remaining])
        except (OSError, ValueError):
            read_failed.append(True)
        finally:
            done.set()
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    reader = threading.Thread(target=consume, name="whynotup-compose-validation-reader", daemon=True)
    reader.start()
    if not done.wait(max(0, deadline - time.monotonic())):
        _stop(process)
        reader.join(timeout=1)
        return _ValidationRead(reason="Docker Compose validation timed out after 10 seconds; validation is incomplete.")
    if len(buffer) > _MAX_STDERR:
        _stop(process)
        reader.join(timeout=1)
        return _ValidationRead(reason="Docker Compose diagnostic output exceeded the 16 KiB limit; validation is incomplete.")
    if read_failed:
        _stop(process)
        return _ValidationRead(reason="Docker Compose diagnostic output could not be read completely; validation is incomplete.")
    try:
        returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        _stop(process)
        return _ValidationRead(reason="Docker Compose validation timed out after 10 seconds or could not complete.")
    return _ValidationRead(returncode=returncode, stderr=bytes(buffer))


def _diagnostic_evidence(project: ComposeProject, stderr: str, returncode: int) -> list[Evidence]:
    """Keep structural error information, never an arbitrary stderr/YAML excerpt.

    Compose can quote interpolated values and custom ${VAR:?message} text.
    Redacting free-form stderr alone cannot guarantee those values are hidden.
    Extract only recognized error classes and configuration paths instead.
    """
    redactor = Redactor(project.secrets)
    try:
        file = str(project.file.relative_to(project.base_dir))
    except ValueError:
        file = project.file.name
    file = redactor.text(file)
    result = [Evidence(f"docker compose config --quiet exited with code {returncode}.", file)]
    seen: set[str] = set()
    for line in stderr[:_MAX_STDERR].splitlines()[:64]:
        reason = next((description for pattern, description in _REASONS if re.search(pattern, line, re.I)), None)
        if reason is None:
            continue
        paths = [path for path in _PATH.findall(line) if path.split(".")[1] in project.raw_services]
        path = paths[0] if paths else None
        summary = f"{path}: {reason}." if path else f"Compose diagnostic: {reason}."
        summary = redactor.text(summary)[:512]
        if summary in seen:
            continue
        seen.add(summary)
        if path:
            _, service, *fields = path.split(".")
            location = project.location(service, *fields)
            result.append(Evidence(summary, redactor.text(location.file), location.line))
        else:
            result.append(Evidence(summary, file))
        if len(result) >= _MAX_EVIDENCE:
            break
    if len(result) == 1:
        result.append(Evidence("Free-form Compose error details were omitted because they may contain configuration values or credentials.", file))
    return result


def validate_compose(project: ComposeProject) -> tuple[list[Finding], list[Check]]:
    """Validate a single file without consulting container state or starting services.

    This function is intended for an explicit CLI opt-in. It never prints full
    resolved Compose configuration, and infrastructure errors are unavailable
    checks rather than findings that blame the configuration.
    """
    env_args = ["--env-file", str(project.interpolation_env_file)] if project.interpolation_env_file is not None else []
    profile_args = [argument for profile in project.active_profiles for argument in ("--profile", profile)]
    argv = [
        "docker", "compose", "--project-directory", str(project.base_dir),
        "-f", str(project.file), *env_args, *profile_args, "config", "--quiet",
    ]
    completed = _capture_validation(argv, project)
    if completed.reason:
        return _unavailable(completed.reason)
    if completed.returncode == 0:
        return [], [Check(_CHECK, "passed", "The installed Docker Compose accepted the selected single-file configuration. Services were not started and application startup was not verified.")]
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode < 0 or completed.returncode in {126, 127} or _UNAVAILABLE.search(stderr[:_MAX_STDERR]):
        return _unavailable("The installed Docker Compose command, required flags, or selected Docker context are unavailable. Validation could not be completed.")
    evidence = _diagnostic_evidence(project, stderr, completed.returncode)
    finding = Finding(
        code="WNU010", severity="error", title="Docker Compose rejected the selected configuration",
        explanation="The installed Compose implementation rejected this file during config --quiet validation. This confirms a validation failure, not an application startup root cause. Only structural error details are included to avoid exposing configuration values.",
        confidence="confirmed", evidence=evidence,
        suggestion=Suggestion("Review the reported configuration path and supported Compose syntax, then rerun validation. The project has not been changed.", file=evidence[0].file),
    )
    return [finding], [Check(_CHECK, "failed", "Docker Compose rejected the selected configuration; see WNU010 for bounded structural evidence.")]
