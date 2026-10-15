# Changelog

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
