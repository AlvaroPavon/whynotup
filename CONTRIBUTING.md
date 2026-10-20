# Contributing to WhyNotUp

Good contributions make a real Compose mistake easier to recognize, explain, and fix. Bug reports, clearer explanations, translations, and new diagnostics are welcome.

## Local development

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
whynotup demo
whynotup review examples/fixed -f compose.yaml --target development
```

Use `.\.venv\Scripts\Activate.ps1` for PowerShell. The demo contains intentional mistakes and returns `1`. Unit tests use fixtures and mocked runtime responses; they do not require a Docker daemon or download container images.

## Report a problem

Static diagnosis and `review` run without Docker. Native `--validate` requires the Docker CLI and Compose v2, but not a daemon. Runtime checks and opt-in log analysis require daemon access. See [Docker tests](docs/DOCKER_TESTS.md) before running integration tests.

Include the WhyNotUp version (`whynotup --version`), Python version, operating system, exact command, expected behavior, and a minimal Compose file. A reviewed JSON or Markdown report can help. Strip credentials, tokens, private hostnames, paths, and personal data before posting any file or report.

For security issues or redaction failures involving sensitive information, follow [SECURITY.md](SECURITY.md) instead of posting secrets in a public issue.

## Add a diagnostic

Explain the concrete mistake and the conditions under which it is actually a mistake. Supply:

- A small broken fixture and the evidence that should identify it.
- A fixed fixture that should pass.
- At least one valid exception to guard against false positives.
- Meaningful tests for the finding, its source location, and any suggested change.
- A documentation update explaining scope and limitations.

Use the shared `Finding`, `Evidence`, and `Suggestion` models. Findings should say why a check fired, identify source evidence, and distinguish confirmed configuration issues from probable causes. Suggested edits must remain suggestions: diagnostics must not modify project files, execute application code, or run mutating Docker commands.

Runtime collection uses argument arrays with `shell=False`, bounded timeouts, and structural data. Do not retain raw Docker inspect configuration or container environment. Logs may be read only through explicit `--logs`/`--all` opt-in, with the service filter applied before reads, bounded lines/bytes/containers, and no raw log retention or output. Emit only recognized pattern families and counts. Unavailable access and skipped checks must be visible rather than appearing to pass.

Native validation must keep `docker compose ... config --quiet` daemon-independent and discard resolved configuration output. Do not copy arbitrary Compose stderr into findings: it can quote credentials or interpolated values. Keep only bounded structural evidence.

Configuration recommendations belong to the deterministic `review` path (`doctor --recommendations`), with `production` and `development` policies. Document every rule and its valid exceptions. Targets must not silently activate Compose profiles, execute fixes, or imply that a recommendation proves a security defect. Treat log matches as possible clues, including historical and recovered failures.

WhyNotUp has no AI/API service, telemetry, accounts, or use quotas. Keep its normal operation local and do not add network or credential requirements to diagnose or review a project.

## Review checklist

- Run `python -m unittest discover -s tests -v`.
- Run the offline demo and confirm that its intentional findings remain understandable.
- Check valid configurations as well as failures.
- Keep text, JSON, and Markdown output subject to the same redaction.
- Document any unsupported Compose feature or uncertainty introduced by the change.
- Keep Python 3.10 compatibility and avoid adding dependencies without a clear need.

Describe the user-visible behavior and validation in the pull request. Small changes that solve one well-defined problem are easiest to review.

## License and attribution

New contributions use the current [LICENSE](LICENSE), SPDX `zlib-acknowledgement`. Keep the original notice, accurately distinguish your changes from the original work, mark altered source versions, and include the required product-documentation acknowledgement: **Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon)**. A fork may use another name. The [licensing guide](docs/LICENSING.md) explains the continuing MIT rights for code previously distributed under MIT; the new terms do not revoke those grants.
