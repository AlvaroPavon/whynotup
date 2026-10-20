# WhyNotUp

[![CI](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/ci.yml)
[![Docker integration](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml/badge.svg)](https://github.com/AlvaroPavon/whynotup/actions/workflows/docker-integration.yml)

**Find what is blocking your Docker Compose app—with evidence and a suggested fix.**

WhyNotUp checks a Compose project for common configuration mistakes, points to the relevant file and line, and explains what to change. Start offline, request a deterministic configuration review, or opt into Compose validation, runtime checks, and bounded log-pattern analysis.

It runs on your machine without AI, API keys, telemetry, accounts, or usage quotas. It sends no project data to a service. Your own machine, Docker environment, CI runner, and hosting provider can still incur their usual costs.

Made by [Álvaro Pavón](https://github.com/AlvaroPavon). [Español](README.es.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Try the demo

Requires Git and Python 3.10 or newer:

```bash
git clone https://github.com/AlvaroPavon/whynotup.git
cd whynotup
python -m venv .venv
source .venv/bin/activate
python -m pip install .
whynotup demo
```

On Windows PowerShell, use `.\.venv\Scripts\Activate.ps1` to activate the environment. The bundled demo works without Docker and intentionally returns exit code `1` because it contains problems. This version is installed from source; a PyPI release is not yet available.

Example demo output, shortened after its first finding:

```text
WhyNotUp 0.2.0 — bundled-demo

Compose file: compose.yaml

4 finding(s): 2 error(s), 2 warning(s).

[ERROR] WNU001 (api): Required variable APP_SECRET is not set
Confidence: confirmed
Compose cannot resolve a required interpolation value.
- Evidence [compose.yaml:5]: Interpolation references APP_SECRET.
Suggestion: Set APP_SECRET in the interpolation environment or the project dotenv file. No value is guessed.
```

Compare the [broken and corrected configuration fixtures](examples/README.md) without starting containers:

```bash
whynotup doctor examples/broken -f compose.yaml  # 3 findings; exit 1
whynotup doctor examples/fixed -f compose.yaml   # No findings; exit 0
```

These counts assume the fixture dotenv values; shell variables take precedence. The fixtures are for diagnostics practice; their API container only sleeps. A clean static report does not establish successful application startup.

## A problem you can recognize

A database is named `db` in Compose, but an API container is configured with:

```yaml
services:
  api:
    image: example/api
    environment:
      DB_HOST: localhost
    depends_on:
      - db
  db:
    image: postgres:16
```

Inside an isolated API container, `localhost` refers to that container. WhyNotUp can identify the database peer and suggest using its service name:

```diff
- DB_HOST: localhost
+ DB_HOST: db
```

The report includes the configuration evidence and its confidence. A suggested change is for you to review; WhyNotUp never applies it. This check identifies a probable misconfiguration, not proof of the application's root cause.

## Check your project

```bash
# Check the configuration in the current directory
whynotup doctor .

# Select one Compose file explicitly
whynotup doctor ./my-app -f compose.yaml

# Include Docker container state and local TCP port checks
whynotup doctor ./my-app -f compose.yaml --runtime

# Ask the installed Compose v2 parser to validate the file; no daemon needed
whynotup doctor ./my-app -f compose.yaml --validate

# Review configuration choices for production, without Docker
whynotup review ./my-app -f compose.yaml --target production
whynotup doctor ./my-app -f compose.yaml --recommendations --target development

# Analyze recent container logs; this also enables runtime checks
whynotup doctor ./my-app -f compose.yaml --logs --log-tail 100 --service api

# Request validation, runtime, logs, and recommendations together
whynotup doctor ./my-app -f compose.yaml --all --target production

# Select profiles or focus the findings on one service
whynotup doctor . --profile development --service api

# Write a report you can review before sharing
whynotup doctor . --format markdown --output diagnostic.md
whynotup doctor . --format json --output diagnostic.json

# Use a specific interpolation environment file
whynotup doctor . --env-file .env.development

# Warnings can fail CI; disabling the threshold does not hide unavailable checks
whynotup review . --target production --fail-on warning
whynotup doctor . --all --fail-on none
```

Static checks and `review` need no Docker installation. `review` is `doctor` with recommendations enabled; its `--target production|development` selects a review policy, not Compose profiles. `doctor --recommendations` uses the same policy. The default target is `production`.

`--validate` invokes `docker compose ... config --quiet` using the selected file, environment, and profiles. It requires the Docker CLI and Compose v2, but no daemon. WhyNotUp's own single-file loader runs first, so unsupported input can be rejected before native validation. Compose acceptance does not establish application startup.

If `COMPOSE_ENV_FILES` is set, select one interpolation file explicitly with `--env-file`. `COMPOSE_DISABLE_ENV_FILE` can disable the default `.env`; an explicit `--env-file` still takes precedence. Native commands preserve the loader's selection.

`--runtime` needs Compose v2 and daemon access. `--logs` implies runtime; `--all` requests validation, runtime, logs, and recommendations, so it also needs daemon access. Runtime operations read state and, only when requested, bounded recent logs; TCP checks temporarily attempt to bind configured host ports. There is no automatic start, stop, restart, build, or repair.

### Checks

| Code | What it checks | Mode |
| --- | --- | --- |
| `WNU001` | Missing or unresolved environment interpolation | Static |
| `WNU002` | Missing required service environment file | Static |
| `WNU003` | Missing host bind-mount source | Static |
| `WNU004` | A configured TCP host port appears occupied | Runtime |
| `WNU005` | A loopback host is used for an identifiable peer service | Static |
| `WNU006` | A peer's published host port is used instead of its container port | Static |
| `WNU007` | A required dependency is unhealthy or not running | Runtime |
| `WNU008` | A required dependency is missing or excluded by profiles | Static |
| `WNU009` | A service container exited or is restarting | Runtime |
| `WNU010` | Installed Compose rejects `config --quiet` validation | `--validate` |

### Optional configuration review

All recommendations are warnings. They describe configuration choices to review; they do not establish vulnerabilities, Internet reachability, readiness, or runtime resource use. `--fail-on warning` includes these warnings in its failure threshold.

| Code | Recommendation | Exceptions and scope |
| --- | --- | --- |
| `WNU101` | Pin an image currently using an implicit tag or `latest` | An image digest is already pinned |
| `WNU102` | Review `privileged: true` | Some host integrations intentionally need it |
| `WNU103` | Review an explicitly configured root user or UID `0` | An omitted user is not treated as proof of root |
| `WNU104` | Review ports bound on all host interfaces | A bind does not prove external access; host-network port mappings are ignored |
| `WNU105` | Review a bind mount of `docker.sock`, including read-only mounts | A named volume is not identified as a host socket |
| `WNU106` | Move sensitive-looking literals or literal interpolation defaults out of `environment` | Pure references, pass-through entries, and `env_file` are excluded; values are omitted |
| `WNU107` | Review dependencies that wait for startup rather than readiness | Optional, completed-job, and missing dependencies are excluded; no health command is invented |
| `WNU108` | Declare CPU and memory limits | Production target only; a recommendation, not a measurement |
| `WNU109` | Review a missing restart policy | Production target only; one-shot jobs may intentionally have none |
| `WNU110` | Review host networking | Host integrations can intentionally need it |
| `WNU111` | Declare persistent storage for a recognized database image | Production target only; persistence outside the known configuration cannot be proven |

### Optional log analysis

Log access is disabled unless you pass `--logs` or `--all`. `--service` restricts the containers selected **before** reading logs. `--log-tail` accepts `1..1000`, defaults to `100`, and bounds requested recent lines per container; collection is also capped at 16 containers and 64 KiB per container. A failed, timed-out, or truncated read marks the check unavailable; coverage notes explain the limits.

Analysis emits warnings with confidence `possible`, a pattern family, and a matching-line count, not raw log lines. It neither writes nor includes raw logs in text, Markdown, or JSON reports. A match may describe a historical, recovered, or dependency failure; it does not establish the current root cause. Earlier or unmatched errors may be missed.

| Code | Recognizable log pattern |
| --- | --- |
| `WNU201` | Connection refused |
| `WNU202` | DNS/name resolution failure |
| `WNU203` | Authentication denied or failed |
| `WNU204` | Permission denied |
| `WNU205` | Out of memory |
| `WNU206` | Address already in use |

Reports distinguish confirmed configuration issues from probable causes, and disclose skipped or unavailable checks. A clean report means that the completed checks found no matching issues; it does not prove that the application will start or behave correctly.

### Output and exit codes

The default output is readable text. `--format json` returns a structured report with `schema_version: "1.0"`; `--format markdown` produces a shareable report. Only `--output` writes a report file. Existing files are never overwritten.

| Exit code | Meaning |
| --- | --- |
| `0` | No finding meets the selected failure threshold |
| `1` | One or more findings meet that threshold |
| `2` | Invalid input or command-line usage |
| `3` | A requested validation, runtime, or log check was unavailable, and no finding met the failure threshold |

Choose the threshold with `--fail-on error`, `--fail-on warning`, or `--fail-on none`. Disabling failure on findings does not hide input errors or unavailable checks. Skipped, incomplete, and limited coverage is also described in the report; read it even when the exit code is `0`.

## Scope and limitations

Version 0.2 adds optional review, native validation, and log clues to common Docker Compose startup diagnostics.

- **One Compose file per run.** WhyNotUp does not merge files. Implicit multi-file configurations and overrides are refused; use `-f` to explicitly scope a check to one file. Results then apply to that file, not to the merged deployment.
- **Configuration and bounded observations.** It does not inspect application source, execute containers, or validate database credentials. Native Compose validation and container log reads are opt-in. Host/network rules use only the configuration context available to them.
- **Local runtime.** Port checks concern the machine running WhyNotUp. They do not establish remote Docker-host port availability or inspect UDP ports. Existing project container mappings help avoid reporting the project's own published ports as conflicts.
- **Explicit coverage.** Compose `include`, service `extends`, and custom YAML tags are rejected. Skipped checks and profile limitations appear in the report. Some projects require checks beyond this tool's scope.
- **Review before sharing.** Text, Markdown, and JSON use the same best-effort redaction. Unrecognized secrets may remain. Read every report before publishing it.

## GitHub Action

The included [composite Action](docs/GITHUB_ACTION.md) adds a diagnostic report to the workflow job summary and exposes numeric finding counts. It supports optional validation, runtime, logs, and recommendations. The CI workflow tests it with `uses: ./`; remote examples use `@main`, which should be replaced with a reviewed full commit SHA for reproducibility. No `v0.2.0` tag is assumed to exist.

## Develop and contribute

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Have a Compose problem this misses? Open an issue with a minimal example and the expected behavior. Remove credentials, private paths, and sensitive hostnames first. A good new check includes both a broken configuration and a valid configuration it should leave alone; see [CONTRIBUTING.md](CONTRIBUTING.md).

Maintainers can follow the [publishing guide](docs/LAUNCH.md). Publishing the repository and running GitHub-hosted checks are separate steps from local validation.

If WhyNotUp helped you, a star makes it easier to find again. Follow [Álvaro Pavón](https://github.com/AlvaroPavon) for new checks and explanations of the problems behind them.

Version 0.2 uses the terms in [LICENSE](LICENSE) (SPDX `zlib-acknowledgement`); see [licensing guidance](docs/LICENSING.md). Product documentation must include: **Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon)**. Preserve the notice, represent original authorship accurately, and mark altered source versions. Forks may use another name. Earlier copies distributed under MIT retain that license.
