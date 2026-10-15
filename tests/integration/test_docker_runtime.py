"""Opt-in live Docker tests. See docs/DOCKER_TESTS.md before running."""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import yaml

from whynotup.compose import load_project
from whynotup.runtime import check_ports, collect_runtime, diagnose_runtime


IMAGE = "alpine:3.21"
ENABLED = os.environ.get("WHYNOTUP_DOCKER_TESTS") == "1"


def command(argv, environment, *, timeout=30):
    """Never echo daemon responses, which could contain endpoint information."""
    try:
        result = subprocess.run(
            argv, env=environment, capture_output=True, text=True,
            shell=False, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssertionError("Required Docker command was unavailable or timed out.") from error
    if result.returncode:
        raise AssertionError(f"Required Docker command failed with exit code {result.returncode}.")
    return result


@unittest.skipUnless(ENABLED, "Set WHYNOTUP_DOCKER_TESTS=1 to create isolated Docker test containers.")
class LiveDockerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Opting in makes unavailable Docker a failure, including a remote/unknown
        # endpoint. Check scope before pulling or creating any resources.
        with tempfile.TemporaryDirectory(prefix="whynotup-docker-check-") as directory:
            file = Path(directory) / "compose.yaml"
            name = "whynotup-it-" + uuid.uuid4().hex
            file.write_text(yaml.safe_dump({"name": name, "services": {"probe": {"image": IMAGE}}}), encoding="utf-8")
            environment = {**os.environ, "COMPOSE_PROJECT_NAME": name, "COMPOSE_PROFILES": ""}
            snapshot = collect_runtime(load_project(file, environ=environment))
            if not snapshot.available:
                raise AssertionError("Live Docker tests were requested, but Docker Compose/daemon access is unavailable.")
            if snapshot.local_daemon is not True:
                raise AssertionError("Live Docker tests require a verified local Docker endpoint.")
            command(["docker", "pull", IMAGE], environment, timeout=120)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="whynotup-docker-test-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.file = self.base / "compose.yaml"
        self.name = "whynotup-it-" + uuid.uuid4().hex
        self.environment = {**os.environ, "COMPOSE_PROJECT_NAME": self.name, "COMPOSE_PROFILES": ""}
        self.started = False
        self.addCleanup(self.cleanup_project)

    def compose(self, *arguments, timeout=45):
        return command([
            "docker", "compose", "--project-directory", str(self.base),
            "--project-name", self.name, "-f", str(self.file), *arguments,
        ], self.environment, timeout=timeout)

    def fixture(self, services):
        # No host mounts, named volumes, privileged mode, or external networks.
        self.file.write_text(yaml.safe_dump({"name": self.name, "services": services}), encoding="utf-8")
        self.project = load_project(self.file, environ=self.environment)
        self.assertEqual(self.project.environment["COMPOSE_PROJECT_NAME"], self.name)
        return self.project

    def up(self, *services):
        self.started = True  # Even a partially failed up must be cleaned up.
        self.compose("up", "-d", "--no-deps", *services)

    def cleanup_project(self):
        if self.started:
            # The random project name and exact temporary file scope cleanup.
            # Never prune resources or stop/remove a user's project.
            self.compose("down", "--timeout", "3", timeout=45)

    def wait_for(self, predicate, *, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = collect_runtime(self.project)
            self.assertTrue(snapshot.available, snapshot.reason)
            self.assertIs(snapshot.local_daemon, True)
            if predicate(snapshot):
                return snapshot
            time.sleep(0.25)
        self.fail("The isolated Docker fixture did not reach its expected state within 45 seconds.")

    @staticmethod
    def sleeper(healthy=True):
        return {
            "image": IMAGE, "command": ["sleep", "300"],
            "healthcheck": {"test": ["CMD", "true" if healthy else "false"], "interval": "1s", "timeout": "1s", "retries": 1},
        }

    def test_healthy_stack_own_published_port_is_not_a_conflict(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        api = self.sleeper()
        api["ports"] = [{"target": 8080, "published": port, "host_ip": "127.0.0.1"}]
        api["depends_on"] = {"db": {"condition": "service_healthy"}}
        self.fixture({"api": api, "db": self.sleeper()})
        self.up("api", "db")
        snapshot = self.wait_for(lambda s: len(s.containers) == 2 and all(c["state"] == "running" and c["health"] == "healthy" for c in s.containers))
        self.assertEqual(diagnose_runtime(self.project, snapshot), [])
        findings, checks = check_ports(self.project, snapshot)
        self.assertEqual(findings, [])
        self.assertTrue(any(c.status == "skipped" and "already owns" in (c.reason or "") for c in checks))

    def test_unhealthy_required_dependency_is_reported(self):
        api = self.sleeper()
        api["depends_on"] = {"db": {"condition": "service_healthy"}}
        self.fixture({"api": api, "db": self.sleeper(healthy=False)})
        # Start only the failing dependency: Compose would refuse to start api
        # while its service_healthy condition is unsatisfied.
        self.up("db")
        snapshot = self.wait_for(lambda s: any(c["service"] == "db" and c["health"] == "unhealthy" for c in s.containers))
        findings = diagnose_runtime(self.project, snapshot)
        self.assertTrue(any(f.code == "WNU007" and f.service == "api" for f in findings))

    def test_nonzero_exited_container_is_reported(self):
        self.fixture({"failed": {"image": IMAGE, "command": ["sh", "-c", "exit 7"], "restart": "no"}})
        self.up("failed")
        snapshot = self.wait_for(lambda s: any(c["state"] == "exited" and c["exit_code"] == 7 for c in s.containers))
        self.assertTrue(any(f.code == "WNU009" and f.service == "failed" for f in diagnose_runtime(self.project, snapshot)))

    def test_restarting_container_is_reported(self):
        self.fixture({"loop": {"image": IMAGE, "command": ["sh", "-c", "sleep 1; exit 9"], "restart": "always"}})
        self.up("loop")
        snapshot = self.wait_for(lambda s: any(c["state"] == "restarting" for c in s.containers))
        self.assertTrue(any(f.code == "WNU009" and f.service == "loop" for f in diagnose_runtime(self.project, snapshot)))

    def test_unrelated_local_listener_is_reported_as_occupied(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            api = self.sleeper()
            api["ports"] = [{"target": 8080, "published": port, "host_ip": "127.0.0.1"}]
            self.fixture({"api": api})
            # This project is never started; the only owner is this test listener.
            snapshot = collect_runtime(self.project)
            self.assertTrue(snapshot.available, snapshot.reason)
            self.assertIs(snapshot.local_daemon, True)
            self.assertEqual(snapshot.containers, [])
            findings, checks = check_ports(self.project, snapshot)
            self.assertTrue(any(f.code == "WNU004" for f in findings))
            self.assertTrue(any(c.status == "failed" for c in checks))


if __name__ == "__main__":
    unittest.main()
