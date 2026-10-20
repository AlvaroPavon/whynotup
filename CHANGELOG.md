# Changelog

## 0.2.0 — Unreleased

- Native `doctor --validate` using `docker compose config --quiet`, without daemon access; validation failures produce `WNU010` with bounded structural evidence instead of copying arbitrary stderr.
- Offline `review` and `doctor --recommendations`, with deterministic `production`/`development` policies and eleven configuration recommendations (`WNU101`–`WNU111`). Targets do not change Compose profile selection.
- Opt-in `--logs` analysis for six recognizable error families (`WNU201`–`WNU206`), with possible confidence, matching-line counts, and no raw-log retention or output. Logs imply runtime and honor service selection before reading.
- Log collection bounded to a configurable tail (`1..1000`, default `100`), 16 containers, 64 KiB per container, and per-read timeouts; incomplete reads disclose unavailable coverage.
- `--all` requests native validation, runtime, logs, and recommendations together. Existing failure thresholds also apply to recommendations and log warnings; unavailable checks remain visible with `--fail-on none`.
- Composite Action inputs for `validate`, `logs`, `log-tail`, `recommendations`, and `target`, with optional checks disabled by default. Remote documentation uses `@main` and recommends pinning a reviewed commit SHA rather than assuming a release tag exists.
- Preserve the explicitly selected interpolation dotenv file in native and runtime Compose commands, preventing an unselected default file from filling missing values.
- Updated English and Spanish usage, rule exceptions, architecture, security boundaries, and publishing guidance. The offline demo remains four intentional findings.
- Current distribution changes to SPDX `zlib-acknowledgement`, requiring product-documentation acknowledgement of Álvaro Pavón (`AlvaroPavon`), accurate original authorship, marked altered sources, and preservation of notices. Renamed forks remain permitted. Earlier code distributed under MIT retains its MIT grant; see [licensing guidance](docs/LICENSING.md).

No PyPI publication or `v0.2.0` GitHub release is claimed by this changelog. Normal operation remains local: no AI, external API, key, account, telemetry, or use quota is required. User-operated infrastructure can have its own costs.

## 0.1.0 — 2026-10-15

Initial source release:

- Nine diagnostic codes for environment, bind mounts, dependency configuration/state, connection endpoints, TCP ports, and failed containers.
- Offline `doctor` and `demo` commands; opt-in, read-only Docker runtime checks.
- File/line evidence, confidence, and review-only change suggestions.
- Consistent text, Markdown, and versioned JSON reports with best-effort redaction.
- Profile selection, explicit single-file scope, dotenv handling, and documented incomplete-check exits.
- Broken/fixed examples, English and Spanish documentation, and a cross-platform CI configuration.
- Composite GitHub Action with job-summary reports and numeric outputs.
- Optional real-Docker integration suite and a dedicated Ubuntu workflow.
- Local release builder that verifies wheel contents, creates source archives, and records SHA-256 checksums.
- Issue and pull-request templates plus a first-release publishing guide.

This version has not been published to PyPI. Live daemon access was unavailable in the development environment; runtime behavior is covered using simulated Docker responses.
