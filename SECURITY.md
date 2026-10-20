# Security

WhyNotUp reads Compose and environment files that may contain credentials. Treat its output as project-sensitive until you have reviewed it.

## Data handling

Static diagnostics and configuration review run locally without Docker, AI, API keys, accounts, telemetry, or network services. Optional `--validate` invokes the installed Compose parser with `config --quiet`; it does not require a daemon or start services. Optional runtime checks read Docker Compose/container state and check local TCP bindings. WhyNotUp does not execute containers, upload reports, or automatically change configuration. Writing a CLI report requires an explicit `--output` destination; the GitHub Action also writes a report to the job summary and displays it in the workflow log.

Container logs are read only when explicitly enabled with `--logs`, `--all`, or the Action's `logs` input. `--logs` implies runtime checks. A service filter is applied before reading its selected containers. The requested tail is `1..1000` lines per container, default `100`; reads are additionally capped at 16 containers and 64 KiB per container, with an 8-second timeout per read. Truncation, inaccessible containers, or other incomplete reads are disclosed as unavailable coverage.

Log data is handled transiently to detect fixed patterns. Raw lines and regex captures are never stored in reports, emitted to the terminal, or written to disk by the collector. Findings contain a pattern family and match count, with confidence `possible`; they can describe an old or recovered error. The Docker daemon may retain its own logs independently. Redaction does not revoke or rotate a credential that was already exposed.

Native Compose stdout is discarded. Validation failures include bounded recognized error classes and structural configuration paths, rather than arbitrary stderr that may quote secrets. Runtime inspection also avoids retaining full container configuration and environment.

All output formats use best-effort redaction of known sensitive values and patterns. This is not a guarantee that every secret, identifier, private path, or business-sensitive value is removed. Review reports before attaching them to issues or posting them publicly. Prefer a minimal fixture with invented credentials when reporting a problem.

Redaction limits do not make it safe to check arbitrary untrusted Compose projects. File references can cause the tool to read accessible local files. Inspect untrusted input and run it in an environment with appropriate access restrictions.

Configuration review is not a security audit. A public port binding does not prove Internet exposure, an omitted user does not prove root, and recognized image/storage patterns cannot determine all deployment requirements. All suggestions require review; none are applied automatically.

## Report a vulnerability

If the published repository has GitHub private vulnerability reporting enabled, use **Security → Report a vulnerability**. Otherwise, use a private contact channel listed on [the maintainer's profile](https://github.com/AlvaroPavon) before sharing sensitive details. Do not put live credentials, unredacted reports, or a sensitive exploit in a public issue.

Include the version, platform, affected behavior, minimal reproduction using dummy secrets, and the likely impact. If a real credential has already been exposed, revoke or rotate it through the relevant provider.

Security fixes target the current development version; older versions have no separate maintenance commitment. See [licensing and attribution](docs/LICENSING.md) for the current distribution terms and the earlier MIT grant.
