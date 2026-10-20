from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from . import __version__
from .compose import ComposeError, load_project
from .diagnostics import diagnose
from .models import Check, Report
from .native import validate_compose
from .logs import collect_log_findings
from .redaction import Redactor
from .reporting import render
from .runtime import check_ports, collect_runtime, diagnose_runtime
from .review import review_project


def _log_tail(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Log tail must be an integer from 1 to 1000.") from None
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("Log tail must be an integer from 1 to 1000.")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whynotup", description="Explain common Docker Compose startup problems with evidence and review-only suggestions.")
    parser.add_argument("--version", action="version", version=f"whynotup {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Diagnose a local Compose project without changing it")
    review = commands.add_parser("review", help="Review configuration and improvements with offline rules; no AI or account")
    for command in (doctor, review):
        command.add_argument("directory", nargs="?", type=Path, default=Path("."), help="Project directory or a Compose file (default: current directory)")
        command.add_argument("-f", "--file", type=Path, help="Select a single Compose file, relative to the project directory")
        command.add_argument("--env-file", type=Path, help="Interpolation dotenv file (default: project .env)")
        command.add_argument("--profile", action="append", help="Activate a Compose profile; repeat as needed, or use '*' for all")
        command.add_argument("--service", help="Show findings for this active service; only its logs are collected")
        command.add_argument("--runtime", action="store_true", help="Also read Docker state and check local TCP port availability")
        command.add_argument("--validate", action="store_true", help="Also run docker compose config --quiet (no daemon needed)")
        command.add_argument("--logs", action="store_true", help="Read bounded container logs for local pattern matching; implies --runtime")
        command.add_argument("--log-tail", type=_log_tail, default=100, metavar="1..1000", help="Maximum log lines requested per container (default: 100; byte limits also apply)")
        command.add_argument("--recommendations", action="store_true", help="Include rules for security, reproducibility, and operational improvements")
        command.add_argument("--target", choices=("development", "production"), default="production", help="Context for recommendations; does not activate Compose profiles (default: production)")
        command.add_argument("--all", dest="all_checks", action="store_true", help="Enable validation, runtime, bounded logs, and recommendations; requires Docker")
    review.set_defaults(recommendations=True)
    for command in (doctor, review, commands.add_parser("demo", help="Run a bundled broken example; no Docker required")):
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
    all_checks = getattr(args, "all_checks", False)
    want_logs = getattr(args, "logs", False) or all_checks
    if getattr(args, "validate", False) or all_checks:
        native_findings, native_checks = validate_compose(project)
        findings += native_findings
        checks += native_checks
    else:
        checks.append(Check("docker-compose-validation", "skipped", "Use --validate to run Docker Compose's configuration validator."))
    if getattr(args, "recommendations", False) or all_checks:
        target = getattr(args, "target", "production")
        findings += review_project(project, target=target)
        checks.append(Check("best-practices", "completed", f"Deterministic recommendations for {target}; review each one in context."))
        notes.append("Recommendations describe configuration tradeoffs, not proof that a setting is insecure or will cause an outage.")
    else:
        checks.append(Check("best-practices", "skipped", "Use review or --recommendations for improvement rules."))
    if args.runtime or want_logs:
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
        if want_logs:
            selected = replace(snapshot, containers=[container for container in snapshot.containers
                               if container.get("service") in project.services
                               and (not args.service or container.get("service") == args.service)])
            log_findings, log_checks = collect_log_findings(project, selected, tail=getattr(args, "log_tail", 100))
            findings += log_findings
            checks += log_checks
    else:
        checks += [Check("docker-runtime", "skipped", "Use --runtime to read container state."),
                   Check("host-ports", "skipped", "Use --runtime to probe local TCP bindings.")]
    if not want_logs:
        checks.append(Check("docker-logs", "skipped", "Use --logs to read a bounded log sample; logs are not read by default."))
    if args.service:
        findings = [f for f in findings if f.service in (None, args.service)]
        notes.append(f"Findings are filtered to service {args.service}; the full Compose file was loaded.")
    findings.sort(key=lambda f: (0 if f.severity == "error" else 1, f.code, f.service or "", f.title))
    notes += ["Single-file scope; image contents, application behavior, and remote hosts are not verified.",
              "All analysis uses deterministic local rules and Docker commands; no AI API, account, or usage quota is required.",
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
        if args.output and args.output.exists():
            raise ComposeError("Output file already exists. Choose a new report path; existing files are never overwritten.")
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
