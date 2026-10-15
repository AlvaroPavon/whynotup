# Broken and corrected Compose configurations

Run from the repository root after installing WhyNotUp:

```sh
whynotup doctor examples/broken
whynotup doctor examples/fixed
```

The broken example yields three static findings:

- **WNU003:** `missing-settings.ini` does not exist and host-path creation is disabled.
- **WNU005:** `DB_HOST=localhost` refers to the API container's loopback namespace; `db` is the candidate PostgreSQL destination.
- **WNU006:** `DB_PORT=55432` uses the database's host publication; its container target is `5432`.

The fixed version supplies a real settings file and changes the destination to `db:5432`. The published host port stays `55432`, because host clients may still need it.

These examples are configuration fixtures, not a real application: the API container only sleeps. No Docker execution is needed to diagnose them. A clean static report does not prove connectivity or successful application startup. The password is an intentionally public training placeholder; do not deploy this configuration.
