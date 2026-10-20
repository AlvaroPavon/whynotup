# Architecture

WhyNotUp is an offline-first Python CLI. Its only runtime Python dependency is PyYAML. It has no AI model, API service, accounts, keys, telemetry, or use quotas. Installation and optional CI infrastructure have their own normal requirements and costs.

1. `compose.py` loads one explicitly scoped Compose file, resolves interpolation and container environment, tracks locations, and selects profiles. Includes, extends, custom YAML tags, recursive aliases, and implicit overrides are refused. This loader runs before optional native validation.
2. `diagnostics.py` analyzes missing bind sources, required dependencies, and identifiable connection settings. Network namespaces, service aliases, optional dependencies, and published/target ports constrain suggestions.
3. `native.py` optionally calls `docker compose ... config --quiet` for the selected file, profiles, and interpolation environment. No daemon is required. It discards stdout and translates recognized stderr error classes/paths into bounded structural evidence. A rejection produces error `WNU010`; unavailable CLI/plugin/context/flags or a timeout produce unavailable coverage, not a configuration accusation.
4. `review.py` supplies deterministic configuration recommendations (`WNU101`–`WNU111`). The `production`/`development` target is a policy, independent of Compose profile selection. All recommendations are warnings. No external model, source-code execution, guessed secret, or automatic edit is involved.
5. `runtime.py` optionally reads Compose/container state with argument arrays, restricted inspect fields, and bounded timeouts. It avoids collecting container environment. It never starts, stops, builds, or modifies containers. Host-port probes are limited to a verified local Docker endpoint.
6. `logs.py` reads bounded recent container logs only with explicit opt-in. Service selection precedes log reads. It emits fixed pattern families and matching-line counts (`WNU201`–`WNU206`), never raw lines or captures. Findings are warnings with confidence `possible`, including the possibility of historical or recovered failures.
7. `models.py` defines source evidence, confidence, suggestions, check coverage, and the versioned report schema. `redaction.py` removes known sensitive values and common credential patterns; `reporting.py` applies it consistently to text, Markdown, and JSON. This is best-effort redaction, not proof that arbitrary input is safe to publish.
8. `cli.py` controls scope, opt-ins, output, and exit status. It writes only an explicitly requested new report path and refuses to overwrite existing files. `action.py` maps validated Action inputs onto the same diagnostic path and writes the job summary and numeric outputs.

## Modes and dependencies

| Request | Additional behavior | Docker requirement |
| --- | --- | --- |
| `doctor` | Static startup diagnostics | None |
| `review` or `doctor --recommendations` | Static diagnostics plus target-specific recommendations | None |
| `--validate` | Native Compose `config --quiet` | CLI and Compose v2, no daemon |
| `--runtime` | Container state and local TCP port checks | Compose v2 and reachable daemon |
| `--logs` | Runtime plus bounded log-pattern analysis | Compose v2 and reachable daemon |
| `--all` | Validation, runtime, logs, and recommendations | Compose v2 and reachable daemon |

`--target` defaults to `production`. Only `WNU108` (CPU/memory limits), `WNU109` (restart policy), and `WNU111` (database persistence) are production-only. The other recommendation codes run for both targets. The [README](../README.md#optional-configuration-review) records rule exceptions: an omitted user is not proof of root; a published port is not proof of Internet access; a one-shot job may intentionally omit restart; referenced environment values are not literal-secret findings.

## Collection bounds and privacy

Log tail requests accept `1..1000`, default `100`. The collector selects at most 16 unique containers of active, selected services; requires structurally valid container IDs; and reads at most 64 KiB per container with an 8-second per-read timeout. A sentinel byte detects overflow. Failed, timed-out, truncated, or skipped reads make log coverage unavailable, with aggregate completion counts. Logs exist transiently during analysis; no raw-log archive or report attachment is created.

Native validation has a 10-second timeout. Stdout is discarded; stderr capture has a 16 KiB limit plus an overflow sentinel. Excess output or a timeout makes coverage unavailable. Error classification emits at most four structural evidence items, each bounded to 512 characters. Unrecognized free-form details are omitted rather than copied after redaction. Both native validation and runtime Compose commands pass the selected existing interpolation dotenv file explicitly, preventing an unselected default `.env` from filling missing values.

`COMPOSE_ENV_FILES` requires an explicit `--env-file` selection to retain the single-dotenv scope. The loader honors `COMPOSE_DISABLE_ENV_FILE` for the default `.env`; an explicit file takes precedence. Child Compose commands remove implicit env-file lists and disable default dotenv loading when the loader selected no file.

## Scope and confidence

One Compose file is checked per run; files are never merged. An explicit `-f` scopes the result to that file, not the complete deployment. A confirmed finding describes observed configuration or state, not a proven complete root cause. Probable connection checks and possible log matches are labeled accordingly. A clean report only describes completed checks; skipped and unavailable checks remain visible.

Exit precedence is input error (`2`), findings at the selected severity threshold (`1`), unavailable requested checks (`3`), then no findings at that threshold (`0`). `--fail-on none` does not suppress unavailable coverage or input errors. JSON retains `schema_version: "1.0"`; consumers should allow additional codes and check names.

## Verification

The stdlib unittest suite uses broken/fixed fixtures and simulated Docker responses for configuration, native validation, review exceptions, bounded logs, redaction, and output/exit behavior. Native Compose parsing can be checked without daemon access. Live container inspection and log reads require a reachable daemon; use the separate [Docker integration guide](DOCKER_TESTS.md) for that evidence.

The current license and required acknowledgement are described in [LICENSING.md](LICENSING.md). Portions Copyright (c) 2026 Álvaro Pavón (AlvaroPavon).
