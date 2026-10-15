"""GitHub Actions adapter. Inputs are data, never interpolated shell code."""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

from .cli import _doctor, _exit_code
from .compose import ComposeError
from .redaction import Redactor
from .reporting import render


def _inputs(environment: dict[str, str]) -> argparse.Namespace:
    runtime = environment.get("WNU_INPUT_RUNTIME", "false").strip().lower()
    threshold = environment.get("WNU_INPUT_FAIL_ON", "error").strip()
    if runtime not in {"true", "false"}:
        raise ComposeError("Action runtime input must be true or false.")
    if threshold not in {"error", "warning", "none"}:
        raise ComposeError("Action fail-on input must be error, warning, or none.")
    profiles = [value.strip() for value in environment.get("WNU_INPUT_PROFILES", "").split(",") if value.strip()]
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*|\*", profile) is None for profile in profiles):
        raise ComposeError("Action profiles input contains an invalid profile name.")
    file = environment.get("WNU_INPUT_COMPOSE_FILE", "").strip()
    env_file = environment.get("WNU_INPUT_ENV_FILE", "").strip()
    return argparse.Namespace(directory=Path(environment.get("WNU_INPUT_PATH", ".")),
                              file=Path(file) if file else None,
                              env_file=Path(env_file) if env_file else None,
                              profile=profiles or None, service=None,
                              runtime=runtime == "true", fail_on=threshold)


def _outputs(environment: dict[str, str], code: int, errors: int, warnings: int) -> None:
    destination = environment.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            # Only bounded numeric values enter the workflow output channel.
            stream.write(f"exit-code={code}\nfinding-count={errors + warnings}\nerror-count={errors}\nwarning-count={warnings}\n")


def _console(text: str) -> None:
    """Keep all untrusted text in one ordered, command-disabled stdout stream."""
    marker = uuid.uuid4().hex
    print(f"::stop-commands::{marker}", flush=True)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    print(f"::{marker}::", flush=True)


def main() -> int:
    environment = dict(os.environ)
    try:
        args = _inputs(environment)
        report, redactor = _doctor(args)
        markdown = render(report, format="markdown", redactor=redactor)
        code = _exit_code(report, args.fail_on)
        errors = sum(item.severity == "error" for item in report.findings)
        warnings = sum(item.severity == "warning" for item in report.findings)
        _outputs(environment, code, errors, warnings)
        if environment.get("GITHUB_STEP_SUMMARY"):
            with Path(environment["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
                stream.write(markdown)
        # Report strings may originate in an untrusted Compose file. Disable
        # workflow command parsing while displaying them in the console.
        _console(render(report, format="text", redactor=redactor))
        return code
    except (ComposeError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, ComposeError) else "Action inputs or report output could not be processed."
        _console(f"whynotup: {Redactor().text(message)}")
        # No attacker-controlled filename/error text enters the separate stderr
        # stream, whose ordering relative to stdout is not guaranteed.
        print("whynotup: Action failed; see the diagnostic above.", file=sys.stderr, flush=True)
        try:
            _outputs(environment, 2, 0, 0)
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
