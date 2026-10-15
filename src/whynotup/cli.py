from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from . import __version__
from .compose import ComposeError, load_project
from .diagnostics import diagnose
from .models import Check, Report
from .redaction import Redactor
from .reporting import render
from .runtime import check_ports, collect_runtime, diagnose_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whynotup", description="Explain common Docker Compose startup problems with evidence and review-only suggestions.")
    parser.add_argument("--version", action="version", version=f"whynotup {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Diagnose a local Compose project without changing it")
    doctor.add_argument("directory", nargs="?", type=Path, default=Path("."), help="Project directory or a Compose file (default: current directory)")
    doctor.add_argument("-f", "--file", type=Path, help="Select a single Compose file, relative to the project directory")
    doctor.add_argument("--env-file", type=Path, help="Interpolation dotenv file (default: project .env)")
    doctor.add_argument("--profile", action="append", help="Activate a Compose profile; repeat as needed, or use '*' for all")
    doctor.add_argument("--service", help="Show findings for this active service (the full file is still loaded)")
    doctor.add_argument("--runtime", action="store_true", help="Also read Docker state and check local TCP port availability")
    for command in (doctor, commands.add_parser("demo", help="Run a bundled broken example; no Docker required")):
        command.add_argument("--format", choices=("text", "json", "markdown"), default="text")
        command.add_argument("--output", type=Path, help="Write a new report file; existing files are never overwritten")
        command.add_argument("--fail-on", choices=("error", "warning", "none"), default="error", help="Finding severity that causes exit 1 (default: error)")
    return parser


def _doctor(args: argparse.Namespace, environ: dict[str, str] | None = None) -> tuple[Report, Redactor]:
    project = load_project(args.directory, file=args.file, env_file=args.env_file,
                           profiles=set(args.profile) if args.profile is not None else None, environ=environ)
    if args.service and args.service not in project.services:
        raise ComposeError("The selected service is absent or inactive in the selected profiles.")
    findings = project.findings + diagnose(project)
    checks = [Check("compose-configuration", "completed"), Check("bind-mounts", "completed"),
              Check("service-connections", "completed"), Check("dependency-configuration", "completed")]
    notes = list(project.notes)
    if args.runtime:
        snapshot = collect_runtime(project)
        checks += snapshot.checks
        if not snapshot.checks:
            checks.append(Check("docker-runtime", "completed" if snapshot.available else "unavailable", snapshot.reason))
        findings += diagnose_runtime(project, snapshot)
        port_findings, port_checks = check_ports(project, snapshot)
        findings += port_findings
        checks += port_checks
        if not snapshot.available:
            notes.append("Container state could not be verified; local port ownership may be uncertain.")
    else:
        checks += [Check("docker-runtime", "skipped", "Use --runtime to read container state."),
                   Check("host-ports", "skipped", "Use --runtime to probe local TCP bindings.")]
    if args.service:
        findings = [f for f in findings if f.service in (None, args.service)]
        notes.append(f"Findings are filtered to service {args.service}; the full Compose file was loaded.")
    findings.sort(key=lambda f: (0 if f.severity == "error" else 1, f.code, f.service or "", f.title))
    notes += ["Single-file scope; image contents, application behavior, and remote hosts are not verified.",
              "Redaction covers known values and common patterns. Review the report before sharing it."]
    return Report(project.base_dir.name, str(project.file.relative_to(project.base_dir)) if project.file.is_relative_to(project.base_dir) else project.file.name,
                  findings, checks, notes), Redactor(project.secrets)


def _exit_code(report: Report, fail_on: str) -> int:
    threshold = {"error"} if fail_on == "error" else {"error", "warning"} if fail_on == "warning" else set()
    if any(f.severity in threshold for f in report.findings):
        return 1
    if any(check.status == "unavailable" for check in report.checks):
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo":
            with tempfile.TemporaryDirectory(prefix="whynotup-demo-") as temporary:
                folder = Path(temporary)
                for name in ("compose.yaml", "demo.env"):
                    source = files("whynotup").joinpath("demo", name)
                    with source.open("rb") as stream, (folder / name).open("wb") as target:
                        shutil.copyfileobj(stream, target)
                doctor_args = argparse.Namespace(directory=folder, file=Path("compose.yaml"), env_file=Path("demo.env"),
                                                 profile=None, service=None, runtime=False)
                report, redactor = _doctor(doctor_args, environ={})
                report.project = "bundled-demo"
        else:
            report, redactor = _doctor(args)
        content = render(report, format=args.format, redactor=redactor)
        if args.output:
            try:
                with args.output.open("x", encoding="utf-8", newline="\n") as target:
                    target.write(content)
            except FileExistsError:
                raise ComposeError("Output file already exists. Choose a new report path; existing files are never overwritten.") from None
            except OSError:
                raise ComposeError("Cannot write the report to the selected path.") from None
            print("Report saved. Review it before sharing.", file=sys.stderr)
        else:
            sys.stdout.write(content)
        return _exit_code(report, args.fail_on)
    except ComposeError as error:
        print(f"whynotup: {Redactor().text(str(error))}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
