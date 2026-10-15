# Validation before first publication

Local verification on 2026-10-15 used Python 3.12 and Docker Compose v2.40.3.

- **122 tests passed**, including CLI, redaction, runtime simulations, Action adapter tests, and ten comparisons against the real Docker Compose configuration parser.
- The installed CLI returned the expected demo/broken/fixed statuses. An Action adapter smoke run generated a job summary and numeric outputs.
- A regression with a newline-bearing malformed env filename confirmed that report errors cannot escape the workflow-command-disabled stdout block.
- Release builds run in a clean temporary source copy. A residual module placed in `build/lib` was excluded from the resulting wheel.
- Wheel/source contents, YAML documents, and Git whitespace checks were inspected locally. The release builder records SHA-256 hashes for the generated archives.

## Still pending

- **Live container tests:** five isolated integration scenarios are included and configured for the Docker integration workflow. They were not executed here because the local daemon socket was not accessible. Their default local run skips rather than creating containers. When explicitly enabled, unavailable Docker is a failure.
- **Hosted GitHub Actions:** workflow configuration and local adapter tests are not evidence that a remote run has passed. Record the target commit's run URLs after publication.
- **Other platforms:** the workflow includes Ubuntu Python 3.10/3.12/3.13 and Windows Python 3.12. Only the local Python 3.12 run is reported above.
- **Publication:** no GitHub or PyPI publication is established by these checks.

Re-run validation on the exact commit and artifacts you intend to publish. See [LAUNCH.md](LAUNCH.md) and [DOCKER_TESTS.md](DOCKER_TESTS.md).
