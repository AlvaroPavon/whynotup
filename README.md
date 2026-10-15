# WhyNotUp

**Find what is blocking your Docker Compose app—with evidence and a suggested fix.**

WhyNotUp checks a Compose project for common configuration mistakes, points to the relevant file and line, and explains what to change. Run an offline configuration check, or add read-only Docker checks when you need to inspect the local runtime.

Made by [Álvaro Pavón](https://github.com/AlvaroPavon). [Español](README.es.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Try the demo

Requires Python 3.10 or newer. From a local copy of this repository:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
whynotup demo
```

On Windows PowerShell, use `.\.venv\Scripts\Activate.ps1` to activate the environment. The bundled demo works without Docker and intentionally returns exit code `1` because it contains problems. This version is installed from source; a PyPI release is not yet available.

Actual demo output, shortened after its first finding:

```text
WhyNotUp 0.1.0 — bundled-demo

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

# Select profiles or focus the findings on one service
whynotup doctor . --profile development --service api

# Write a report you can review before sharing
whynotup doctor . --format markdown --output diagnostic.md
whynotup doctor . --format json --output diagnostic.json

# Use a specific interpolation environment file
whynotup doctor . --env-file .env.development
```

Static checks need no Docker installation. `--runtime` requires Docker with Compose v2 and access to its daemon. Runtime commands only read Compose/container state; TCP checks temporarily attempt to bind the configured host ports. There is no automatic start, stop, restart, build, or repair.

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

Reports distinguish confirmed configuration issues from probable causes, and disclose skipped or unavailable checks. A clean report means that the completed checks found no matching issues; it does not prove that the application will start or behave correctly.

### Output and exit codes

The default output is readable text. `--format json` returns a structured report with `schema_version: "1.0"`; `--format markdown` produces a shareable report. Only `--output` writes a report file. Existing files are never overwritten.

| Exit code | Meaning |
| --- | --- |
| `0` | No finding meets the selected failure threshold |
| `1` | One or more findings meet that threshold |
| `2` | Invalid input or command-line usage |
| `3` | Requested runtime checks were incomplete or unavailable, and no finding met the failure threshold |

Choose the threshold with `--fail-on error`, `--fail-on warning`, or `--fail-on none`. Disabling failure on findings does not hide input errors or unavailable runtime checks.

## Scope and limitations

This is an initial release focused on common Docker Compose startup problems.

- **One Compose file per run.** WhyNotUp does not merge files. Implicit multi-file configurations and overrides are refused; use `-f` to explicitly scope a check to one file. Results then apply to that file, not to the merged deployment.
- **Configuration, not application execution.** It does not inspect application source, execute containers, collect logs, or validate database credentials. Host/network rules use only the configuration context available to them.
- **Local runtime.** Port checks concern the machine running WhyNotUp. They do not establish remote Docker-host port availability or inspect UDP ports. Existing project container mappings help avoid reporting the project's own published ports as conflicts.
- **Explicit coverage.** Compose `include`, service `extends`, and custom YAML tags are rejected. Skipped checks and profile limitations appear in the report. Some projects require checks beyond this tool's scope.
- **Review before sharing.** Text, Markdown, and JSON use the same best-effort redaction. Unrecognized secrets may remain. Read every report before publishing it.

## GitHub Action

The included [composite Action](docs/GITHUB_ACTION.md) adds a diagnostic report to the workflow job summary and exposes numeric finding counts. The CI workflow is configured to test it with `uses: ./`. Remote use requires publishing the repository and release ref first.

## Develop and contribute

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Have a Compose problem this misses? Open an issue with a minimal example and the expected behavior. Remove credentials, private paths, and sensitive hostnames first. A good new check includes both a broken configuration and a valid configuration it should leave alone; see [CONTRIBUTING.md](CONTRIBUTING.md).

Maintainers can follow the [first-release publishing guide](docs/LAUNCH.md). Publishing the repository and running GitHub-hosted checks are separate steps from local validation.

If WhyNotUp helped you, a star makes it easier to find again. Follow [Álvaro Pavón](https://github.com/AlvaroPavon) for new checks and explanations of the problems behind them.

MIT licensed. See [LICENSE](LICENSE).
