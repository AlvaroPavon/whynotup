from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whynotup.models import ComposeProject, RuntimeSnapshot, SourceLocation
from whynotup.runtime import _INSPECT_FORMAT, check_ports, collect_runtime, diagnose_runtime


CONTAINER_ID = "a" * 64
OTHER_ID = "b" * 64


def go_compiler():
    executable = shutil.which("go")
    if executable:
        try:
            result = subprocess.run(
                [executable, "version"], capture_output=True, text=True, shell=False, timeout=5,
                env={**os.environ, "GOENV": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off"},
            )
            if result.returncode == 0 and result.stdout.startswith("go version "):
                return executable
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


GO_COMPILER = go_compiler()


def project(services):
    return ComposeProject(
        base_dir=Path("/tmp/example"), file=Path("/tmp/example/compose.yaml"),
        services=services, raw_services=services, environment={},
        service_environment={}, locations={
            ("services", name): SourceLocation("compose.yaml", 3 + index)
            for index, name in enumerate(services)
        },
    )


def container(service="api", state="running", health=None, exit_code=0, ports=None):
    return {
        "id": CONTAINER_ID, "name": f"example-{service}-1", "service": service,
        "state": state, "health": health, "exit_code": exit_code, "ports": ports or [],
    }


def response(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class InspectTemplateRegressionTests(unittest.TestCase):
    @unittest.skipUnless(GO_COMPILER, "Optional offline template regression needs a Go compiler; real Docker CI covers containers without healthchecks.")
    def test_restricted_template_handles_missing_health_with_docker_strict_keys(self):
        # Docker's raw inspect fallback uses Go templates with missingkey=error.
        # Executing the real template catches errors that canned Docker JSON
        # responses cannot catch. No Docker, network, or third-party Go code.
        source = r'''
package main
import ("bytes"; "encoding/json"; "os"; "text/template")
func main() {
    var input struct { Template string; Cases []map[string]interface{} }
    if err := json.NewDecoder(os.Stdin).Decode(&input); err != nil { panic(err) }
    functions := template.FuncMap{"json": func(value interface{}) string {
        encoded, err := json.Marshal(value); if err != nil { panic(err) }; return string(encoded)
    }}
    parsed, err := template.New("inspect").Funcs(functions).Option("missingkey=error").Parse(input.Template)
    if err != nil { panic(err) }
    results := []map[string]string{}
    for _, value := range input.Cases {
        var output bytes.Buffer
        execution := parsed.Execute(&output, value)
        result := map[string]string{"output": output.String(), "error": ""}
        if execution != nil { result["error"] = execution.Error() }
        results = append(results, result)
    }
    if err := json.NewEncoder(os.Stdout).Encode(results); err != nil { panic(err) }
}
'''
        cases = []
        for state, health, exit_code in [("running", "healthy", 0), ("running", "unhealthy", 0), ("exited", None, 7), ("restarting", None, 9)]:
            observed = {"Status": state, "ExitCode": exit_code}
            if health is not None:
                observed["Health"] = {"Status": health}
            cases.append({
                "Id": CONTAINER_ID, "Name": "/example-api-1",
                "Config": {"Labels": {"com.docker.compose.service": "api"}, "Env": ["TOKEN=not-for-the-report"]},
                "State": observed, "NetworkSettings": {"Ports": {}},
            })
        with tempfile.TemporaryDirectory(prefix="whynotup-template-test-") as directory:
            file = Path(directory) / "inspect.go"
            file.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [GO_COMPILER, "run", str(file)],
                input=json.dumps({"Template": _INSPECT_FORMAT, "Cases": cases}),
                text=True, capture_output=True, shell=False, timeout=90,
                env={**os.environ, "GOENV": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "CGO_ENABLED": "0", "GOCACHE": str(Path(directory) / "cache")},
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = json.loads(completed.stdout)
        self.assertEqual(len(results), len(cases))
        for result, expected in zip(results, cases):
            with self.subTest(state=expected["State"]["Status"]):
                self.assertEqual(result["error"], "")
                actual = json.loads(result["output"])
                self.assertEqual(actual["state"], expected["State"]["Status"])
                self.assertEqual(actual["exit_code"], expected["State"]["ExitCode"])
                self.assertEqual(actual["health"], expected["State"].get("Health", {}).get("Status"))
                self.assertNotIn("not-for-the-report", result["output"])


class RuntimeCollectionTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    @patch("whynotup.runtime.subprocess.run")
    def test_restricted_inspection_never_requests_environment_or_logs(self, run):
        raw = {
            "id": CONTAINER_ID, "name": "/example-api-1", "service": "api",
            "state": "running", "health": "healthy", "exit_code": 0,
            "ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}], "81/tcp": None},
            "Config": {"Env": ["TOKEN=secret-value"]}, "Logs": "secret-value",
        }
        run.side_effect = [response(CONTAINER_ID[:12] + "\n"), response(json.dumps(raw) + "\n"), response('"unix:///var/run/docker.sock"')]
        snapshot = collect_runtime(project({"api": {}}))
        self.assertTrue(snapshot.available)
        self.assertIs(snapshot.local_daemon, True)
        self.assertEqual(snapshot.containers, [container(health="healthy", ports=[{
            "host_ip": "127.0.0.1", "host_port": 8080, "target_port": 80, "protocol": "tcp",
        }])])
        self.assertNotIn("secret-value", repr(snapshot))
        self.assertNotIn("Config", snapshot.containers[0])
        inspect_argv = run.call_args_list[1].args[0]
        self.assertEqual(inspect_argv[:3], ["docker", "inspect", "--format"])
        self.assertNotIn(".Env", inspect_argv[3])
        for call in run.call_args_list:
            self.assertIs(call.kwargs["shell"], False)
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertIsInstance(call.args[0], list)
        self.assertEqual(run.call_args_list[0].args[0][-3:], ["ps", "--all", "--quiet"])

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_loader_interpolation_environment_is_used_without_mutating_process(self, run):
        compose = project({})
        compose.environment = {"CUSTOM_PROJECT_PORT": "9876", "COMPOSE_PROFILES": "ignored"}
        compose.active_profiles = ("dev", "metrics")
        collect_runtime(compose)
        self.assertEqual(run.call_args.kwargs["env"]["CUSTOM_PROJECT_PORT"], "9876")
        self.assertEqual(run.call_args.kwargs["env"]["COMPOSE_PROFILES"], "dev,metrics")
        self.assertIn("--profile", run.call_args_list[0].args[0])
        self.assertIn("metrics", run.call_args_list[0].args[0])
        self.assertNotIn("--env-file", run.call_args_list[0].args[0])

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_selected_interpolation_env_file_prevents_implicit_dotenv_reload(self, run):
        compose = project({})
        compose.interpolation_env_file = Path("/tmp/example/env files/selected.env")
        compose.active_profiles = ("development",)
        snapshot = collect_runtime(compose)
        self.assertTrue(snapshot.available)
        self.assertEqual(run.call_args_list[0].args[0], [
            "docker", "compose", "--project-directory", str(compose.base_dir),
            "-f", str(compose.file),
            "--env-file", str(compose.interpolation_env_file),
            "--profile", "development", "ps", "--all", "--quiet",
        ])
        # Context inspection is a Docker operation, not a Compose operation.
        self.assertNotIn("--env-file", run.call_args_list[1].args[0])

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_absent_selected_dotenv_disables_implicit_sources_in_child_only(self, run):
        compose = project({})
        compose.environment = {"COMPOSE_ENV_FILES": "project-extra.env", "COMPOSE_DISABLE_ENV_FILE": "false"}
        with patch.dict("os.environ", {"COMPOSE_ENV_FILES": "shell-extra.env"}):
            collect_runtime(compose)
            self.assertEqual(os.environ["COMPOSE_ENV_FILES"], "shell-extra.env")
        environment = run.call_args_list[0].kwargs["env"]
        self.assertNotIn("COMPOSE_ENV_FILES", environment)
        self.assertEqual(environment["COMPOSE_DISABLE_ENV_FILE"], "1")
        self.assertEqual(compose.environment["COMPOSE_ENV_FILES"], "project-extra.env")

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_explicit_env_file_keeps_disable_flag_but_removes_implicit_files(self, run):
        compose = project({})
        compose.interpolation_env_file = Path("/tmp/example/selected.env")
        compose.environment = {"COMPOSE_ENV_FILES": "ignored.env", "COMPOSE_DISABLE_ENV_FILE": "true"}
        collect_runtime(compose)
        environment = run.call_args_list[0].kwargs["env"]
        self.assertNotIn("COMPOSE_ENV_FILES", environment)
        self.assertEqual(environment["COMPOSE_DISABLE_ENV_FILE"], "true")
        self.assertIn(str(compose.interpolation_env_file), run.call_args_list[0].args[0])

    @patch("whynotup.runtime.subprocess.run")
    def test_remote_context_remains_available_without_leaking_endpoint(self, run):
        run.side_effect = [response(), response('"ssh://private.example.test"')]
        snapshot = collect_runtime(project({}))
        self.assertTrue(snapshot.available)
        self.assertIs(snapshot.local_daemon, False)
        self.assertNotIn("private.example.test", repr(snapshot))

    @patch("whynotup.runtime.subprocess.run")
    def test_context_takes_precedence_over_docker_host(self, run):
        compose = project({})
        compose.environment = {"DOCKER_HOST": "tcp://127.0.0.1:2375", "DOCKER_CONTEXT": "remote-context"}
        run.side_effect = [response(), response('"tcp://private.example.test:2376"')]
        snapshot = collect_runtime(compose)
        self.assertIs(snapshot.local_daemon, False)
        self.assertIn("remote-context", run.call_args_list[1].args[0])

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_explicit_loopback_tcp_host_is_local_without_context_request(self, run):
        compose = project({})
        compose.environment = {"DOCKER_HOST": "tcp://[::1]:2376"}
        snapshot = collect_runtime(compose)
        self.assertIs(snapshot.local_daemon, True)
        run.assert_called_once()

    @patch("whynotup.runtime.subprocess.run")
    def test_unavailable_context_does_not_invalidate_container_state(self, run):
        run.side_effect = [response(), response(returncode=1, stderr="secret-endpoint")]
        snapshot = collect_runtime(project({}))
        self.assertTrue(snapshot.available)
        self.assertIsNone(snapshot.local_daemon)
        self.assertNotIn("secret-endpoint", repr(snapshot))

    @patch("whynotup.runtime.subprocess.run")
    def test_inspection_must_cover_each_requested_id(self, run):
        duplicate_prefix = dict(container(), id="a" * 12 + "c" * 52)
        run.side_effect = [response(CONTAINER_ID[:12] + "\n" + OTHER_ID[:12]), response(json.dumps(container()) + "\n" + json.dumps(duplicate_prefix))]
        self.assertFalse(collect_runtime(project({})).available)

    @patch("whynotup.runtime.subprocess.run", return_value=response())
    def test_empty_project_snapshot_is_available_but_not_verified_healthy(self, run):
        snapshot = collect_runtime(project({"api": {}}))
        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.containers, [])
        self.assertIn("startup was not verified", snapshot.checks[0].reason)
        self.assertEqual(run.call_count, 2)

    @patch("whynotup.runtime.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_cli_is_unavailable(self, run):
        snapshot = collect_runtime(project({}))
        self.assertFalse(snapshot.available)
        self.assertEqual(snapshot.checks[0].status, "unavailable")

    @patch("whynotup.runtime.subprocess.run", return_value=response(returncode=1, stderr="PASSWORD=secret-value"))
    def test_failed_daemon_does_not_echo_raw_error(self, run):
        snapshot = collect_runtime(project({}))
        self.assertFalse(snapshot.available)
        self.assertNotIn("secret-value", repr(snapshot))

    @patch("whynotup.runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 10, stderr="secret-value"))
    def test_timeout_reports_safe_unavailability(self, run):
        snapshot = collect_runtime(project({}))
        self.assertFalse(snapshot.available)
        self.assertIn("timed out", snapshot.reason)
        self.assertNotIn("secret-value", repr(snapshot))

    @patch("whynotup.runtime.subprocess.run")
    def test_invalid_ps_identifier_is_not_passed_to_inspect(self, run):
        run.return_value = response("--malicious-argument\n")
        snapshot = collect_runtime(project({}))
        self.assertFalse(snapshot.available)
        run.assert_called_once()

    @patch("whynotup.runtime.subprocess.run")
    def test_missing_inspection_result_is_unavailable(self, run):
        run.side_effect = [response(CONTAINER_ID + "\n" + OTHER_ID), response(json.dumps(container()))]
        self.assertFalse(collect_runtime(project({})).available)

    @patch("whynotup.runtime.subprocess.run")
    def test_malformed_inspect_response_does_not_escape(self, run):
        run.side_effect = [response(CONTAINER_ID), response("PASSWORD=secret-value")]
        snapshot = collect_runtime(project({}))
        self.assertFalse(snapshot.available)
        self.assertNotIn("secret-value", repr(snapshot))


class RuntimeDiagnosisTests(unittest.TestCase):
    def test_required_healthy_dependency_has_state_evidence_and_location(self):
        compose = project({"api": {"depends_on": {"db": {"condition": "service_healthy"}}}, "db": {}})
        snapshot = RuntimeSnapshot(True, [container("db", health="unhealthy")])
        findings = diagnose_runtime(compose, snapshot)
        self.assertEqual([f.code for f in findings], ["WNU007"])
        self.assertEqual(findings[0].service, "api")
        self.assertEqual(findings[0].evidence[0].file, "compose.yaml")
        self.assertIn("health=unhealthy", findings[0].evidence[1].summary)

    def test_healthy_dependency_does_not_produce_error(self):
        compose = project({"api": {"depends_on": {"db": {"condition": "service_healthy"}}}, "db": {}})
        self.assertEqual(diagnose_runtime(compose, RuntimeSnapshot(True, [container("db", health="healthy")])), [])

    def test_running_container_without_health_cannot_satisfy_healthy_condition(self):
        compose = project({"api": {"depends_on": {"db": {"condition": "service_healthy"}}}, "db": {}})
        self.assertEqual(diagnose_runtime(compose, RuntimeSnapshot(True, [container("db")]))[0].code, "WNU007")

    def test_missing_required_container_is_explicit_and_optional_is_ignored(self):
        compose = project({"api": {"depends_on": {"db": {}, "optional": {"required": False}}}, "db": {}, "optional": {}})
        findings = diagnose_runtime(compose, RuntimeSnapshot(True))
        self.assertEqual(len(findings), 1)
        self.assertIn("no container", findings[0].evidence[-1].summary)

    def test_completed_successfully_job_is_valid_even_though_exited(self):
        compose = project({"api": {"depends_on": {"migrate": {"condition": "service_completed_successfully"}}}, "migrate": {}})
        self.assertEqual(diagnose_runtime(compose, RuntimeSnapshot(True, [container("migrate", "exited")])), [])

    def test_failed_job_and_restarting_service_have_observed_state_findings(self):
        compose = project({"api": {"depends_on": ["db"]}, "db": {}})
        findings = diagnose_runtime(compose, RuntimeSnapshot(True, [container("api", "restarting", exit_code=1), container("db", "exited", exit_code=2)]))
        self.assertEqual([f.code for f in findings].count("WNU007"), 1)
        self.assertEqual([f.code for f in findings].count("WNU009"), 2)
        self.assertTrue(any("exit_code=2" in e.summary for f in findings for e in f.evidence))

    def test_unavailable_runtime_and_profile_excluded_dependency_are_not_guessed(self):
        compose = project({"api": {"depends_on": ["inactive"]}})
        self.assertEqual(diagnose_runtime(compose, RuntimeSnapshot(False)), [])
        self.assertEqual(diagnose_runtime(compose, RuntimeSnapshot(True)), [])

    def test_zero_exit_is_not_assumed_to_be_application_failure(self):
        self.assertEqual(diagnose_runtime(project({"job": {}}), RuntimeSnapshot(True, [container("job", "exited")])), [])


class PortCheckTests(unittest.TestCase):
    def setUp(self):
        self.binds = []
        self.closed = []
        self.socket_families = []
        self.errors = {}

    def fake_socket(self, family, kind):
        self.socket_families.append((family, kind))
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(side_effect=lambda *args: self.closed.append(True))

        def bind(address):
            self.binds.append(address)
            if address in self.errors:
                raise OSError(self.errors[address], "synthetic error")
        probe.bind.side_effect = bind
        return probe

    def run_ports(self, services, snapshot=None):
        with patch("whynotup.runtime.socket.socket", side_effect=self.fake_socket):
            return check_ports(project(services), snapshot)

    def test_distinct_host_bindings_and_ipv6_use_correct_addresses_and_close(self):
        findings, checks = self.run_ports({"api": {"ports": ["127.0.0.1:8080:80", "[::1]:8080:80", {"target": 81, "published": 8081, "host_ip": "127.0.0.2"}]}})
        self.assertEqual(findings, [])
        self.assertEqual(self.binds, [("127.0.0.1", 8080), ("::1", 8080), ("127.0.0.2", 8081)])
        self.assertEqual(self.socket_families[1], (socket.AF_INET6, socket.SOCK_STREAM))
        self.assertEqual(len(self.closed), 3)
        self.assertTrue(all(c.status == "passed" for c in checks))

    def test_occupied_port_has_evidence_but_does_not_claim_verified_owner(self):
        self.errors[("0.0.0.0", 8080)] = errno.EADDRINUSE
        findings, checks = self.run_ports({"api": {"ports": ["8080:80"]}})
        self.assertEqual(findings[0].code, "WNU004")
        self.assertEqual(findings[0].confidence, "probable")
        self.assertIn("owner was not verified", findings[0].explanation)
        self.assertEqual(checks[0].status, "failed")
        self.assertEqual(len(self.closed), 1)

    def test_unavailable_address_and_permissions_are_not_occupied_errors(self):
        self.errors[("192.0.2.1", 8080)] = errno.EADDRNOTAVAIL
        self.errors[("0.0.0.0", 81)] = errno.EACCES
        findings, checks = self.run_ports({"api": {"ports": ["192.0.2.1:8080:80", "81:80"]}})
        self.assertEqual(findings, [])
        self.assertEqual([c.status for c in checks], ["unavailable", "unavailable"])

    def test_own_running_publication_skips_bind_but_other_service_does_not(self):
        published = {"host_ip": "0.0.0.0", "host_port": 8080, "target_port": 80, "protocol": "tcp"}
        snapshot = RuntimeSnapshot(True, [container(ports=[published])], local_daemon=True)
        self.errors[("0.0.0.0", 8080)] = errno.EADDRINUSE
        findings, checks = self.run_ports({"api": {"ports": ["8080:80"]}, "web": {"ports": ["8080:80"]}}, snapshot)
        self.assertEqual(self.binds, [("0.0.0.0", 8080)])
        self.assertEqual([c.status for c in checks], ["skipped", "failed"])
        self.assertEqual(findings[0].service, "web")

    def test_unknown_endpoint_does_not_probe_this_hosts_ports(self):
        published = {"host_ip": "0.0.0.0", "host_port": 8080, "target_port": 80, "protocol": "tcp"}
        findings, checks = self.run_ports({"api": {"ports": ["8080:80"]}}, RuntimeSnapshot(False, [container(ports=[published])]))
        self.assertEqual(self.binds, [])
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")

    def test_remote_own_mapping_is_neither_probed_nor_reported_as_local_owner(self):
        published = {"host_ip": "0.0.0.0", "host_port": 8080, "target_port": 80, "protocol": "tcp"}
        findings, checks = self.run_ports({"api": {"ports": ["8080:80"]}}, RuntimeSnapshot(True, [container(ports=[published])], local_daemon=False))
        self.assertEqual(self.binds, [])
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("remote", checks[0].reason)

    def test_udp_and_dynamic_publications_are_explicitly_skipped(self):
        findings, checks = self.run_ports({"api": {"ports": ["8080:80/udp", "80", {"target": 80}, "0:80", "1-1000:1-1000/udp"]}})
        self.assertEqual(findings, [])
        self.assertEqual(self.binds, [])
        self.assertTrue(all(c.status == "skipped" for c in checks))
        self.assertIn("UDP", checks[0].reason)

    def test_host_network_does_not_use_published_mapping(self):
        findings, checks = self.run_ports({"api": {"network_mode": "host", "ports": ["8080:80"]}})
        self.assertEqual(findings, [])
        self.assertEqual(self.binds, [])
        self.assertEqual(checks[0].status, "skipped")

    def test_pairwise_ranges_probe_each_publication(self):
        findings, checks = self.run_ports({"api": {"ports": ["8080-8082:80-82"]}})
        self.assertEqual(findings, [])
        self.assertEqual(self.binds, [("0.0.0.0", 8080), ("0.0.0.0", 8081), ("0.0.0.0", 8082)])
        self.assertEqual(len(checks), 3)

    def test_allocation_range_only_needs_one_free_port(self):
        self.errors[("0.0.0.0", 8080)] = errno.EADDRINUSE
        findings, checks = self.run_ports({"api": {"ports": [{"target": 80, "published": "8080-8082"}]}})
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "passed")
        self.assertEqual(self.binds, [("0.0.0.0", 8080), ("0.0.0.0", 8081)])

    def test_occupied_allocation_range_produces_one_finding(self):
        for port in range(8080, 8083):
            self.errors[("0.0.0.0", port)] = errno.EADDRINUSE
        findings, checks = self.run_ports({"api": {"ports": ["8080-8082:80"]}})
        self.assertEqual(len(findings), 1)
        self.assertIn("8080-8082", findings[0].title)
        self.assertEqual(checks[0].status, "failed")

    def test_allocation_range_with_unprobeable_port_is_not_declared_occupied(self):
        self.errors[("0.0.0.0", 8080)] = errno.EADDRINUSE
        self.errors[("0.0.0.0", 8081)] = errno.EACCES
        findings, checks = self.run_ports({"api": {"ports": ["8080-8081:80"]}})
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")

    def test_port_ranges_and_total_probes_are_bounded(self):
        findings, checks = self.run_ports({"api": {"ports": ["1-1000:1-1000"] + [f"{10000 + index}:80" for index in range(130)]}})
        self.assertEqual(findings, [])
        self.assertEqual(len(self.binds), 128)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertEqual(checks[-1].status, "unavailable")

    def test_invalid_host_and_incompatible_range_are_explicitly_unavailable(self):
        findings, checks = self.run_ports({"api": {"ports": ["example.invalid:8080:80", "8080-8081:80-82"]}})
        self.assertEqual(findings, [])
        self.assertEqual(self.binds, [])
        self.assertTrue(all(c.status == "unavailable" for c in checks))


if __name__ == "__main__":
    unittest.main()
