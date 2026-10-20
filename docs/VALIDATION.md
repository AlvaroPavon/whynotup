# Validation record

## Version 0.2 local verification — 2026-10-20

The implementation candidate was checked using Python 3.12 and Docker Compose v2.40.3.

- `python -m unittest discover -s tests -q` discovered **203 tests: 202 passed and one optional Go-template regression was skipped** because a Go compiler was unavailable. The suite includes ten comparisons with the real Compose configuration parser, new review rules, bounded log/native subprocess handling, CLI/Action inputs, redaction, and dotenv selection.
- The real native integration test passed with `WHYNOTUP_DOCKER_TESTS=1` and `test_docker_native.py`. It deliberately selects an unreachable daemon endpoint: the installed Compose parser accepts the default dotenv and rejects an explicitly selected empty file instead of silently reloading the default.
- Additional daemon-independent checks confirmed that implicit `COMPOSE_ENV_FILES` requires explicit selection, `COMPOSE_DISABLE_ENV_FILE` suppresses the default dotenv, and an explicit dotenv takes precedence.
- The built wheel was installed into a separate environment and exercised outside the checkout. Version `0.2.0`, the four-finding offline demo, and production review all worked. No project files were changed.
- Wheel metadata uses `License-Expression: zlib-acknowledgement`. The packaged `LICENSE`, `NOTICE`, and historical `licenses/MIT-0.1.0.txt` matched their source files byte for byte. The release builder verifies these notices in both wheel and source distributions and records SHA-256 hashes.
- Git whitespace checks passed. The historical MIT notice matches the version published at commit `7944542`.

Hosted checks for the new commit are a separate verification step. The workflows cover Ubuntu Python 3.10/3.12/3.13, Windows Python 3.12, installed distributions, the composite Action including native validation/recommendations, and eight Docker integration scenarios. The live-container cases were not run against the restricted local daemon. When explicitly enabled, unavailable Docker fails the integration suite rather than silently skipping it.

## Version 0.1 hosted verification

Published commit [`7944542`](https://github.com/AlvaroPavon/whynotup/commit/794454274584e48e56a6ca43bb067c9e12b5e2c4) passed both hosted workflows:

- [CI: Ubuntu, Windows, Action, and package build](https://github.com/AlvaroPavon/whynotup/actions/runs/38722686539).
- [Docker integration: five live-container scenarios](https://github.com/AlvaroPavon/whynotup/actions/runs/38722686661).

Those results apply to that earlier commit, not automatically to version 0.2. Check the exact target commit's current runs before claiming hosted validation. No PyPI publication or release tag is established by this record. See [LAUNCH.md](LAUNCH.md) and [DOCKER_TESTS.md](DOCKER_TESTS.md).
