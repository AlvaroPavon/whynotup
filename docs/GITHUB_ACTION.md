# GitHub Action

The repository includes a composite Action in `action.yml`. It installs the checked-out source, runs a diagnosis, and writes the redacted Markdown report to the workflow job summary. It does not upload the report or apply suggested fixes.

The local CI workflow tests it against the valid fixture using `uses: ./`. After this repository and a reviewed release ref have been published, another repository can use it as follows:

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
      # Replace the release tag with a reviewed full commit SHA for pinning.
      - uses: AlvaroPavon/whynotup@v0.1.0
        id: compose
        with:
          path: .
          compose-file: compose.yaml
          runtime: 'false'
          fail-on: error
```

The remote repository/ref above is a publishing target, not a claim that it is currently available. Until publication, test the Action from a checkout of WhyNotUp itself.

## Inputs

| Input | Default | Purpose |
| --- | --- | --- |
| `path` | `.` | Project directory relative to the workflow workspace |
| `compose-file` | Auto-discover | Explicit single Compose file relative to `path` |
| `env-file` | Project `.env` | Interpolation environment file relative to `path` |
| `profiles` | Normal profile selection | Comma-separated profiles; `*` selects all |
| `runtime` | `false` | Read local Docker state and probe local TCP bindings |
| `fail-on` | `error` | `error`, `warning`, or `none` |

Runtime checks require a reachable Docker daemon. The Action does not start your application's services. An Ubuntu runner will usually have no project containers unless another step explicitly started them.

## Outputs and failure behavior

`exit-code`, `finding-count`, `error-count`, and `warning-count` are numeric outputs. Exit statuses match the CLI, including `3` for incomplete requested runtime checks. A finding at the selected threshold fails the step; use GitHub's `continue-on-error` only when that is the intended workflow policy.

The adapter passes inputs as data rather than shell fragments, and disables workflow command parsing while displaying report text. Text and job-summary Markdown use the same redaction as the CLI. Review reports before sharing them; redaction does not cover every possible secret.

This Action selects Python 3.12 and installs WhyNotUp into that interpreter. Run application environment setup afterward if the job needs a different Python setup.
