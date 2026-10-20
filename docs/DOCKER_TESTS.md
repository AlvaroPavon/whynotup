# Real Docker and Compose integration tests

The regular unit suite uses fixtures and simulated Docker responses. The opt-in integration files in `tests/integration/test_docker_*.py` exercise the installed Compose parser and, where required, real containers and local TCP ports.

## Run the complete suite

Use Python 3.10+, Docker Engine with an accessible local daemon, and Docker Compose v2. The runtime suite explicitly pulls `alpine:3.21` once per run, including when a cached copy exists, so registry network access is required.

From the repository root:

```sh
python -m pip install -e .
WHYNOTUP_DOCKER_TESTS=1 python -m unittest discover -s tests/integration -p 'test_docker_*.py' -v
```

Only the exact value `WHYNOTUP_DOCKER_TESTS=1` enables these tests. Without it, direct integration discovery reports skips. The regular `python -m unittest discover -s tests -v` command does not recurse into the integration directory because it has no `__init__.py`.

Once enabled, missing Docker/Compose or inaccessible daemon access fails the required tests instead of silently skipping them. The runtime suite also rejects unsupported, remote, or unknown daemon endpoints so its local port probes refer to the same machine as the containers.

## Parser-only regression without a daemon

The separate native-validation file can run with only the Docker CLI and Compose v2 installed:

```sh
WHYNOTUP_DOCKER_TESTS=1 python -m unittest discover -s tests/integration -p 'test_docker_native.py' -v
```

Its test deliberately selects an absent daemon socket. It verifies that a populated default `.env` cannot satisfy a required variable when an explicitly selected, empty dotenv file should be used. It creates no containers, pulls no image, and does not require daemon access.

## Covered scenarios

`test_docker_runtime.py` contains seven scenarios:

- A healthy service owns its published port without a false `WNU004` conflict.
- An unhealthy required dependency produces `WNU007`.
- A nonzero-exited container produces `WNU009`, including a container without a healthcheck.
- A restarting container produces `WNU009`.
- The real log reader recognizes connection-refused and DNS messages from both output streams, while synthetic secrets stay out of findings and every report format.
- An independent local TCP listener produces `WNU004`.
- Native `config --quiet` accepts a valid configuration and rejects an unsupported property with `WNU010` while a deliberately absent daemon is selected.

`test_docker_native.py` adds the selected-dotenv regression above. The complete integration command therefore currently discovers eight tests. A passed parser-only test does not establish that the real-container scenarios passed.

## Isolation and cleanup

Runtime fixtures use temporary Compose files and random project names beginning with `whynotup-it-`. Commands and readiness waits are bounded. Test services use no host mounts, named volumes, privileged mode, or external networks. Cleanup runs `docker compose down` only for the exact temporary project; it never prunes resources or stops unrelated projects. The pulled image may remain cached afterward.

The full opt-in suite creates and removes test containers and networks. This test harness is distinct from the WhyNotUp CLI, whose diagnostics do not start, build, stop, or modify application services. CLI log collection requires explicit opt-in and reports only pattern families/counts, not raw messages.

## Continuous integration and evidence

[The Docker integration workflow](../.github/workflows/docker-integration.yml) runs on pushes, pull requests, and manual dispatches. It uses Ubuntu, Python 3.12, an editable installation, explicit Docker/Compose availability checks, and the complete integration command above. The job has a ten-minute timeout and read-only repository permissions.

Verify results for the exact target commit. Unit-test simulations, parser-only checks, and successful older workflow runs are separate evidence; none proves that the current real-container suite passed. Record successful run URLs after the new checks finish. Local daemon restrictions can prevent running container tests in a development environment, so do not report them as locally executed when they only ran in CI.
