# Security

WhyNotUp reads Compose and environment files that may contain credentials. Treat its output as project-sensitive until you have reviewed it.

## Data handling

The static checks run locally. Optional runtime checks read Docker Compose/container state and check local TCP bindings. WhyNotUp does not execute containers, upload reports, collect container logs, or automatically change project configuration. Writing a report requires an explicit `--output` destination.

All output formats use best-effort redaction of known sensitive values and patterns. This is not a guarantee that every secret, identifier, private path, or business-sensitive value is removed. Review reports before attaching them to issues or posting them publicly. Prefer a minimal fixture with invented credentials when reporting a problem.

Redaction limits do not make it safe to check arbitrary untrusted Compose projects. File references can cause the tool to read accessible local files. Inspect untrusted input and run it in an environment with appropriate access restrictions.

## Report a vulnerability

If the published repository has GitHub private vulnerability reporting enabled, use **Security → Report a vulnerability**. Otherwise, use a private contact channel listed on [the maintainer's profile](https://github.com/AlvaroPavon) before sharing sensitive details. Do not put live credentials, unredacted reports, or a sensitive exploit in a public issue.

Include the version, platform, affected behavior, minimal reproduction using dummy secrets, and the likely impact. If a real credential has already been exposed, revoke or rotate it through the relevant provider.

This project is an initial release. Security fixes target the current development version; older versions have no separate maintenance commitment.
