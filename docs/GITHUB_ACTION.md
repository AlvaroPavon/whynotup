# GitHub Action

The composite Action in `action.yml` installs its own checked-out source, runs the same diagnostics as the CLI, and writes a redacted Markdown report to the job summary. It also displays the report in the workflow log. It does not send reports to a separate service or apply fixes.

The repository CI tests it using `uses: ./`. The remote example below uses the mutable `main` branch; replace it with a reviewed **full commit SHA** for reproducible execution. It does not assume a `v0.2.0` release or PyPI package exists.

```yaml
name: Compose checks
on: [push, pull_request]
permissions:
  contents: read
jobs:
  diagnose:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Replace main with a reviewed full commit SHA before pinning a workflow.
      - uses: AlvaroPavon/whynotup@main
        id: compose
        with:
          path: .
          compose-file: compose.yaml
          validate: 'true'
          recommendations: 'true'
          target: production
          runtime: 'false'
          logs: 'false'
          log-tail: '100'
          fail-on: error
```

This example requests native validation and production recommendations without starting services or reading logs. Native validation needs the Docker CLI and Compose v2, but not daemon access. If Compose v2 is absent, that requested check is unavailable; it does not silently pass.

## Inputs

| Input | Default | Purpose |
| --- | --- | --- |
| `path` | `.` | Project directory relative to the workflow workspace |
| `compose-file` | Auto-discover | Explicit single Compose file relative to `path` |
| `env-file` | Project `.env`, unless disabled in the environment | Explicit single interpolation environment file relative to `path` |
| `profiles` | Normal profile selection | Comma-separated profiles; `*` selects all |
| `validate` | `false` | Run native Compose `config --quiet` validation |
| `runtime` | `false` | Read Docker state and probe local TCP bindings |
| `logs` | `false` | Analyze bounded recent log patterns; implies runtime |
| `log-tail` | `100` | Requested recent lines per container, integer `1..1000` |
| `recommendations` | `false` | Add deterministic configuration recommendations |
| `target` | `production` | Review policy: `production` or `development`; does not select Compose profiles |
| `fail-on` | `error` | Finding threshold: `error`, `warning`, or `none` |

Boolean inputs accept `true` or `false`. The CLI's `--all` shorthand is not an Action input: set `validate`, `runtime`, `logs`, and `recommendations` individually when all are wanted. With all four disabled, the Action performs static diagnostics without Docker.

An explicit `env-file` takes precedence over `COMPOSE_DISABLE_ENV_FILE` and selects the one dotenv file used by the loader and native Docker commands. A nonempty implicit `COMPOSE_ENV_FILES` setting requires an explicit selection instead of silently loading multiple files.

Runtime and log checks require a reachable Docker daemon. The Action does not start project services. A fresh runner has no project containers unless another step explicitly starts them. To analyze existing containers, opt in explicitly:

```yaml
      - uses: AlvaroPavon/whynotup@main # Pin a reviewed full commit SHA.
        with:
          path: .
          compose-file: compose.yaml
          validate: 'true'
          runtime: 'true'
          logs: 'true'
          log-tail: '100'
          recommendations: 'true'
          target: development
          fail-on: warning
```

All log reads remain bounded to at most 16 containers and 64 KiB per container. Findings show fixed pattern families and counts, never raw log lines. Inaccessible or truncated reads are reported as unavailable coverage. Recommendations and log clues are warnings; `fail-on: warning` can fail the step on either.

## Outputs and failure behavior

`exit-code`, `finding-count`, `error-count`, and `warning-count` are numeric outputs. Statuses match the CLI:

| Exit | Meaning |
| --- | --- |
| `0` | No finding meets the selected threshold and no requested check is unavailable |
| `1` | A finding meets the selected threshold |
| `2` | Invalid input, command usage, or report-output error |
| `3` | A requested check is unavailable and no finding meets the threshold |

`fail-on: none` does not suppress status `2` or `3`. Review coverage notes even after a successful step. Use GitHub's `continue-on-error` only when that is the intended workflow policy.

Inputs are passed as data rather than shell fragments. Workflow command parsing is disabled while displaying report text. All report formats use the same best-effort redaction; unrecognized secrets or sensitive project details may remain. Treat summaries and workflow logs as project-sensitive until reviewed.

The Action selects Python 3.12 and installs WhyNotUp into that interpreter. Run application environment setup afterward if the job needs a different Python setup. It uses no AI/API service, key, or use quota; GitHub Actions and your runner infrastructure retain their normal billing and resource policies.

Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon). See [licensing guidance](LICENSING.md).
