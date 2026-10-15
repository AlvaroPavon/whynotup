# Real Docker integration tests

The regular unit suite uses simulated Docker responses. The opt-in suite in
`tests/integration/test_docker_runtime.py` checks runtime diagnostics against
real containers and local TCP ports.

## Run locally

Use Python 3.10 or newer, Docker Engine with a local daemon, and Docker Compose
v2. Docker must be accessible to your current user. The suite pulls
`alpine:3.21` once per run and requires registry network access, including
when an older copy is already cached.

From the repository root:

```sh
python -m pip install -e .
WHYNOTUP_DOCKER_TESTS=1 python -m unittest discover -s tests/integration -p 'test_docker_*.py' -v
```

Only the exact value `WHYNOTUP_DOCKER_TESTS=1` enables these tests. Without it,
directly discovering the integration suite reports skips. The standard command
`python -m unittest discover -s tests -v` does not recurse into this integration
directory because it has no `__init__.py`.

Once enabled, missing Docker, an inaccessible daemon, an unsupported or remote
context, and unavailable Compose fail the suite instead of silently skipping
it. Run against a local Docker daemon so local port checks refer to the same
machine as the containers.

## What the suite checks

- A healthy running service owns its published port and does not produce a
  false `WNU004` port-conflict finding.
- An unhealthy dependency produces `WNU007` with runtime evidence.
- A container that exits with a nonzero code and a restarting container
  produce `WNU009`.
- A TCP port occupied by an independent local listener produces `WNU004`.

The suite pulls `alpine:3.21` once, uses temporary Compose files, and gives each
test an isolated random project name beginning with `whynotup-it-`. Docker
commands and readiness waits are bounded. Test services use no volumes or
host mounts. Cleanup runs `docker compose down` for the exact test project;
it does not prune Docker resources or stop unrelated projects. The pulled
image can remain in the local image cache.

Running the opt-in suite creates and removes test containers and networks.
The WhyNotUp CLI itself remains read-only: diagnostics inspect configuration
and runtime state without starting, changing, or stopping application services.

## Continuous integration and validation status

[The Docker integration workflow](../.github/workflows/docker-integration.yml)
runs on pushes, pull requests, and manual dispatches. It uses an Ubuntu runner,
Python 3.12, an editable package installation, explicit Docker/Compose checks,
and the command above with the opt-in flag enabled. The job has a ten-minute
timeout and read-only repository permissions.

**The real Docker suite has not been executed in the development environment:**
Docker daemon access was unavailable or denied. Real-container verification
is pending a successful CI run or an authorized local run. Simulated unit-test
results alone do not establish that these real Docker scenarios pass.
