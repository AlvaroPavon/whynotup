# Architecture

WhyNotUp is an offline-first Python CLI. Its only runtime Python dependency is PyYAML.

1. `compose.py` loads one explicitly scoped Compose configuration, resolves interpolation and container environment, tracks source locations, and selects profiles. Input errors never echo configuration lines. Includes, extends, custom YAML tags, recursive aliases, and implicit overrides are refused.
2. `diagnostics.py` analyzes missing bind sources, required dependencies, and identifiable service connection settings. Network namespaces, service aliases, optional dependencies, and published/target ports constrain suggestions.
3. `runtime.py` optionally reads Compose/container state using argument arrays, a restricted Docker inspect template, and bounded timeouts. It never collects container environment or logs and never starts, builds, or modifies containers. Host-port probes are limited to a verified local Docker endpoint.
4. `models.py` defines source evidence, confidence, suggestions, check coverage, and the versioned report schema.
5. `redaction.py` removes known sensitive values and common credential patterns. `reporting.py` applies it before producing text, Markdown, or JSON. This is best-effort redaction, not a proof that arbitrary input is safe to publish.
6. `cli.py` controls scope, output, and exit status. It writes only an explicitly requested new report path and refuses to overwrite existing files.

## Scope and confidence

A confirmed finding describes an observed configuration or runtime state. It is not a claim that the application's complete root cause has been proven. Heuristic connection checks are marked probable. Skipped and unavailable checks are visible even when no findings are returned.

Exit status precedence is input error (2), findings at the selected severity threshold (1), unavailable requested checks (3), then no findings at the threshold (0). The JSON report has `schema_version: "1.0"`.

## Verification

The stdlib unittest suite covers broken/fixed fixture pairs, valid networking exceptions, interpolation and dotenv precedence, fake Docker responses, bounded socket probes, redaction, and CLI output/exit behavior. Docker Compose's real configuration parser can validate fixture interpolation without access to the Docker daemon. Live container inspection requires an accessible daemon and is separate from those checks.
