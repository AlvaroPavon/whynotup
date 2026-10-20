from __future__ import annotations

import io
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from whynotup.models import ComposeProject, SourceLocation
from whynotup.native import validate_compose


def project():
    services = {"api": {"image": "alpine:3.21"}}
    return ComposeProject(
        base_dir=Path("/tmp/native-test"), file=Path("/tmp/native-test/compose.yaml"),
        services=services, raw_services=services, environment={}, service_environment={},
        locations={("services", "api"): SourceLocation("compose.yaml", 3)},
    )


class TrackingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []
        self.bytes_read = 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        data = super().read(size)
        self.bytes_read += len(data)
        return data


class FakeProcess:
    def __init__(self, stderr="", returncode=0, *, wait_timeout=False, stream=None):
        self.stderr = stream if stream is not None else TrackingStream(stderr.encode())
        self.returncode = returncode
        self.wait_timeout = wait_timeout
        self.killed = False
        self.wait_timeouts = []

    def kill(self):
        self.killed = True
        if hasattr(self.stderr, "release"):
            self.stderr.release.set()

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.wait_timeout and not self.killed:
            raise subprocess.TimeoutExpired("private-command", timeout, stderr="private-error")
        return -9 if self.killed else self.returncode


def response(stderr="", returncode=0, **kwargs):
    return FakeProcess(stderr, returncode, **kwargs)


class NativeValidationTests(unittest.TestCase):
    @patch("whynotup.native.subprocess.Popen", return_value=response())
    def test_read_only_argv_profiles_and_interpolation_environment(self, run):
        compose = project()
        compose.active_profiles = ("development", "metrics")
        compose.environment = {"APP_PORT": "8123", "COMPOSE_PROFILES": "wrong"}
        findings, checks = validate_compose(compose)
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].name, "docker-compose-validation")
        self.assertEqual(checks[0].status, "passed")
        self.assertEqual(run.call_args.args[0], [
            "docker", "compose", "--project-directory", str(compose.base_dir), "-f", str(compose.file),
            "--profile", "development", "--profile", "metrics", "config", "--quiet",
        ])
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertTrue(0 < run.return_value.wait_timeouts[0] <= 10)
        self.assertEqual(run.call_args.kwargs["bufsize"], 0)
        self.assertEqual(run.call_args.kwargs["env"]["APP_PORT"], "8123")
        self.assertEqual(run.call_args.kwargs["env"]["COMPOSE_PROFILES"], "development,metrics")
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("SHOULD_NEVER_BE_REPORTED", repr((findings, checks)))
        run.assert_called_once()

    @patch("whynotup.native.subprocess.Popen", return_value=response())
    def test_selected_interpolation_file_is_explicit_before_profiles(self, run):
        compose = project()
        compose.interpolation_env_file = Path("/tmp/native-test/selected.env")
        compose.active_profiles = ("development",)
        self.assertEqual(validate_compose(compose)[1][0].status, "passed")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--env-file") + 1], str(compose.interpolation_env_file))
        self.assertLess(argv.index("--env-file"), argv.index("--profile"))

    @patch("whynotup.native.subprocess.Popen", side_effect=FileNotFoundError("private-path"))
    def test_missing_cli_is_unavailable_without_exposing_exception(self, run):
        findings, checks = validate_compose(project())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertNotIn("private-path", repr(checks))

    @patch("whynotup.native.subprocess.Popen", return_value=response(wait_timeout=True))
    def test_timeout_is_not_configuration_failure(self, run):
        findings, checks = validate_compose(project())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("10 seconds", checks[0].reason)
        self.assertNotIn("SECRET_VALUE", repr(checks))
        self.assertTrue(run.return_value.killed)

    @patch("whynotup.native.subprocess.Popen", side_effect=PermissionError("private-path"))
    def test_execution_permission_error_is_unavailable(self, run):
        self.assertEqual(validate_compose(project())[1][0].status, "unavailable")

    def test_missing_plugin_unknown_flags_and_bad_context_are_not_project_errors(self):
        errors = [
            "docker: 'compose' is not a docker command.",
            "docker: unknown command: docker compose",
            'unknown command "compose" for "docker"',
            "unknown flag: --quiet", "unknown flag: --profile",
            "unknown shorthand flag: 'q' in -q", "flag provided but not defined: -quiet",
            'unable to resolve docker endpoint: context "private-context" does not exist',
            "Cannot connect to the Docker daemon at unix:///private/socket",
        ]
        for error in errors:
            with self.subTest(error=error), patch("whynotup.native.subprocess.Popen", return_value=response(error, 1)):
                findings, checks = validate_compose(project())
                self.assertEqual(findings, [])
                self.assertEqual(checks[0].status, "unavailable")
                self.assertNotIn("private-context", repr(checks))
                self.assertNotIn("private/socket", repr(checks))

    def test_signal_and_cannot_execute_exit_codes_are_unavailable(self):
        for code in [-9, 126, 127]:
            with self.subTest(code=code), patch("whynotup.native.subprocess.Popen", return_value=response("", code)):
                self.assertEqual(validate_compose(project())[1][0].status, "unavailable")

    @patch("whynotup.native.subprocess.Popen", return_value=response("validating /tmp/native-test/compose.yaml: services.api Additional property unexpected is not allowed", 1))
    def test_schema_rejection_is_confirmed_with_structural_source_evidence(self, run):
        findings, checks = validate_compose(project())
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual((finding.code, finding.severity, finding.confidence), ("WNU010", "error", "confirmed"))
        self.assertEqual(checks[0].status, "failed")
        self.assertIn("services.api", finding.evidence[1].summary)
        self.assertIn("unsupported configuration property", finding.evidence[1].summary)
        self.assertEqual(finding.evidence[1].file, "compose.yaml")
        self.assertEqual(finding.evidence[1].line, 3)

    def test_config_values_urls_custom_messages_and_stdout_are_never_echoed(self):
        compose = project()
        compose.secrets = {"known-secret-value"}
        error = """services.api.environment: invalid type: known-secret-value
RAW_PASSWORD=unregistered-value
postgres://unregistered-user:unregistered-password@db/app
TOKEN=another-value
error while interpolating: MY_CUSTOM_PRIVATE_MESSAGE
    command: echo SOME_RAW_CONFIG_VALUE
"""
        with patch("whynotup.native.subprocess.Popen", return_value=response(error, 1)):
            result = repr(validate_compose(compose))
        for value in ["known-secret-value", "unregistered-value", "unregistered-user", "unregistered-password", "another-value", "MY_CUSTOM_PRIVATE_MESSAGE", "SOME_RAW_CONFIG_VALUE", "SHOULD_NEVER_BE_REPORTED"]:
            self.assertNotIn(value, result)
        self.assertIn("invalid type", result)

    def test_known_secret_configuration_path_is_redacted_before_output(self):
        compose = project()
        compose.secrets = {"privatefield"}
        with patch("whynotup.native.subprocess.Popen", return_value=response("services.api.privatefield must be a string", 1)):
            result = repr(validate_compose(compose))
        self.assertNotIn("privatefield", result)
        self.assertIn("REDACTED", result)

    def test_unknown_error_retains_exit_evidence_without_raw_text(self):
        with patch("whynotup.native.subprocess.Popen", return_value=response("highly-private-error-details", 1)):
            findings, checks = validate_compose(project())
        self.assertEqual(findings[0].code, "WNU010")
        self.assertIn("exited with code 1", findings[0].evidence[0].summary)
        self.assertIn("omitted", findings[0].evidence[1].summary)
        self.assertNotIn("highly-private-error-details", repr((findings, checks)))

    def test_large_stderr_is_bounded_and_deduplicated(self):
        error = "services.api.ports invalid published port SECRET_VALUE\n" * 100
        with patch("whynotup.native.subprocess.Popen", return_value=response(error, 1)):
            findings, checks = validate_compose(project())
        self.assertLessEqual(len(findings[0].evidence), 4)
        self.assertLess(len(repr((findings, checks))), 3000)
        self.assertNotIn("SECRET_VALUE", repr(findings))

    def test_zero_exit_warning_does_not_fabricate_validation_failure(self):
        with patch("whynotup.native.subprocess.Popen", return_value=response("warning: PASSWORD=private", 0)):
            findings, checks = validate_compose(project())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "passed")
        self.assertNotIn("private", repr(checks))

    def test_gigantic_output_is_never_stored_or_classified_as_validated(self):
        class GiantStream:
            def __init__(self):
                self.bytes_read = 0
                self.read_sizes = []
                self.closed = False

            def read(self, size):
                self.read_sizes.append(size)
                self.bytes_read += size
                return b"x" * size  # Simulates unlimited output without allocating it.

            def close(self):
                self.closed = True

        for code in [0, 1]:
            stream = GiantStream()
            process = response(returncode=code, stream=stream)
            with self.subTest(returncode=code), patch("whynotup.native.subprocess.Popen", return_value=process):
                findings, checks = validate_compose(project())
            self.assertEqual(findings, [])
            self.assertEqual(checks[0].status, "unavailable")
            self.assertIn("16 KiB", checks[0].reason)
            self.assertEqual(stream.bytes_read, 16384 + 1)
            self.assertTrue(all(0 < size <= 4096 for size in stream.read_sizes))
            self.assertTrue(stream.closed)
            self.assertTrue(process.killed)

    def test_read_timeout_kills_child_and_releases_blocked_reader(self):
        class BlockedStream:
            def __init__(self):
                self.release = threading.Event()
                self.closed = False

            def read(self, size):
                self.release.wait(1)
                return b""

            def close(self):
                self.closed = True

        stream = BlockedStream()
        process = response(stream=stream)
        with patch("whynotup.native.subprocess.Popen", return_value=process), patch("whynotup.native._TIMEOUT", 0.01):
            findings, checks = validate_compose(project())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("timed out", checks[0].reason)
        self.assertTrue(process.killed)
        self.assertTrue(stream.closed)

    def test_exact_limit_with_eof_is_complete_and_bounded(self):
        process = response("x" * 16384)
        with patch("whynotup.native.subprocess.Popen", return_value=process):
            findings, checks = validate_compose(project())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "passed")
        self.assertEqual(process.stderr.bytes_read, 16384)
        self.assertFalse(process.killed)


if __name__ == "__main__":
    unittest.main()
