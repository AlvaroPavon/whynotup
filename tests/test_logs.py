from __future__ import annotations

import io
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from whynotup.logs import _MAX_BYTES, collect_log_findings
from whynotup.models import ComposeProject, RuntimeSnapshot


IDENTIFIER = "a" * 64


def project(services=None):
    services = services if services is not None else {"api": {}}
    return ComposeProject(Path("/tmp/example"), Path("/tmp/example/compose.yaml"),
                          services, services, {}, {}, {})


def snapshot(*containers):
    return RuntimeSnapshot(True, list(containers or [{"id": IDENTIFIER, "service": "api"}]))


class Process:
    def __init__(self, data=b"", *, returncode=0):
        self.stdout = io.BytesIO(data)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class LogTests(unittest.TestCase):
    def collect(self, data, *, compose=None, state=None, tail=100, returncode=0):
        process = Process(data, returncode=returncode)
        with patch("whynotup.logs.subprocess.Popen", return_value=process) as popen:
            findings, checks = collect_log_findings(compose or project(), state or snapshot(), tail=tail)
        return findings, checks, process, popen

    def test_six_families_have_safe_counts_and_no_log_contents(self):
        sensitive = "unpublishable-user-and-password"
        data = (f"ECONNREFUSED {sensitive}\nConnection refused {sensitive}\n"
                f"getaddrinfo ENOTFOUND {sensitive}\npassword authentication failed for user {sensitive}\n"
                f"Permission denied {sensitive}\nOOMKilled {sensitive}\nEADDRINUSE {sensitive}\n").encode()
        findings, checks, _, _ = self.collect(data, tail=50)
        self.assertEqual([item.code for item in findings], [f"WNU20{index}" for index in range(1, 7)])
        self.assertEqual(checks[0].status, "completed")
        self.assertIn("2 matching line(s)", findings[0].evidence[0].summary)
        self.assertNotIn(sensitive, repr(findings) + repr(checks))
        self.assertNotIn("password authentication failed", repr(findings))
        for finding in findings:
            self.assertEqual(finding.confidence, "possible")
            self.assertEqual(finding.severity, "warning")
            self.assertIn("last 50 log lines", finding.explanation)
            self.assertIn("historical", finding.explanation)

    def test_safe_command_and_environment_without_streaming_or_shell(self):
        compose = project()
        compose.environment = {"DOCKER_CONTEXT": "selected-context"}
        _, _, _, popen = self.collect(b"", compose=compose, tail=27)
        argv = popen.call_args.args[0]
        self.assertEqual(argv, ["docker", "logs", "--tail", "27", IDENTIFIER])
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["env"]["DOCKER_CONTEXT"], "selected-context")
        self.assertNotIn("--follow", argv)

    def test_basic_negation_and_zero_counters_do_not_report_findings(self):
        findings, _, _, _ = self.collect(b"No connection refused errors\n"
                                         b"0 DNS resolution failures\n"
                                         b"without authentication failure\n"
                                         b"permission denied count: 0\n"
                                         b"out of memory: false\n"
                                         b"OOMKilled=false\n"
                                         b"EADDRINUSE errors=0\n"
                                         b"0 errors; all services healthy\n")
        self.assertEqual(findings, [])

    def test_unrelated_success_counter_does_not_hide_real_error(self):
        findings, _, _, _ = self.collect(b"0 errors before retry; connection refused\n")
        self.assertEqual([finding.code for finding in findings], ["WNU201"])

    def test_variants_and_plain_words_avoid_generic_false_positives(self):
        data = (b"Temporary failure in name resolution\nName or service not known\nlookup db: no such host\n"
                b"UnknownHostException\nAccess denied for user\nNOAUTH Authentication required\n"
                b"WRONGPASS invalid username-password pair\nOperation not permitted\n"
                b"out-of-memory\nport is already allocated\n")
        findings, _, _, _ = self.collect(data)
        self.assertEqual([item.code for item in findings], ["WNU202", "WNU203", "WNU204", "WNU205", "WNU206"])
        clean, _, _, _ = self.collect(b"failed\nkilled\nuser denied\nfile not found\nexit 137\n0 errors\n")
        self.assertEqual(clean, [])

    def test_unavailable_snapshot_never_reads_logs(self):
        with patch("whynotup.logs.subprocess.Popen") as popen:
            findings, checks = collect_log_findings(project(), RuntimeSnapshot(False))
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        popen.assert_not_called()

    def test_invalid_tail_is_bounded_and_never_passed_to_docker(self):
        for tail in (0, -1, 1001, True, "100", "--follow"):
            with self.subTest(tail=tail), patch("whynotup.logs.subprocess.Popen") as popen:
                findings, checks = collect_log_findings(project(), snapshot(), tail=tail)
                self.assertEqual(findings, [])
                self.assertEqual(checks[0].status, "unavailable")
                popen.assert_not_called()

    def test_ids_services_and_duplicate_entries_are_checked(self):
        state = snapshot({"id": "--malicious", "service": "api"},
                         {"id": "b" * 64, "service": "inactive"},
                         {"id": IDENTIFIER, "service": "api"},
                         {"id": IDENTIFIER, "service": "api"})
        findings, checks, _, popen = self.collect(b"", state=state)
        self.assertEqual(findings, [])
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-1], IDENTIFIER)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("1 invalid snapshot", checks[0].reason)

    def test_no_active_containers_is_explicit(self):
        with patch("whynotup.logs.subprocess.Popen") as popen:
            findings, checks = collect_log_findings(project(), RuntimeSnapshot(True))
        self.assertEqual(findings, [])
        self.assertIn("No containers", checks[0].reason)
        popen.assert_not_called()

    def test_container_limit_marks_coverage_incomplete(self):
        containers = [{"id": f"{index:064x}", "service": "api"} for index in range(20)]
        with patch("whynotup.logs.subprocess.Popen", side_effect=lambda *args, **kwargs: Process()) as popen:
            findings, checks = collect_log_findings(project(), snapshot(*containers))
        self.assertEqual(findings, [])
        self.assertEqual(popen.call_count, 16)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("4 skipped", checks[0].reason)
        self.assertIn("Completed 16 of 16", checks[0].reason)

    def test_huge_output_is_bounded_killed_and_disclosed(self):
        class EndlessStream:
            def __init__(self):
                self.read_bytes = 0

            def read(self, count):
                self.read_bytes += count
                return (b"connection refused\n" * (count // 19 + 1))[:count]

            def close(self):
                pass

        process = Process()
        process.stdout = EndlessStream()
        with patch("whynotup.logs.subprocess.Popen", return_value=process):
            findings, checks = collect_log_findings(project(), snapshot())
        self.assertLessEqual(process.stdout.read_bytes, _MAX_BYTES + 1)
        self.assertTrue(process.killed)
        self.assertTrue(findings)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("1 truncated", checks[0].reason)

    def test_cli_failure_is_not_mistaken_for_an_application_pattern(self):
        findings, checks, _, _ = self.collect(b"daemon connection refused password=private-value", returncode=1)
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertNotIn("private-value", repr(checks))

    def test_equal_output_limit_is_complete_and_invalid_utf8_is_safe(self):
        data = b"permission denied\n\xff" + b"x" * (_MAX_BYTES - 19)
        self.assertEqual(len(data), _MAX_BYTES)
        findings, checks, process, _ = self.collect(data)
        self.assertEqual([item.code for item in findings], ["WNU204"])
        self.assertEqual(checks[0].status, "completed")
        self.assertFalse(process.killed)

    def test_partial_failure_retains_other_container_findings(self):
        processes = [Process(b"permission denied token=private-value", returncode=1),
                     Process(b"Connection refused token=private-value")]
        state = snapshot({"id": IDENTIFIER, "service": "api"}, {"id": "b" * 64, "service": "api"})
        with patch("whynotup.logs.subprocess.Popen", side_effect=processes):
            findings, checks = collect_log_findings(project(), state)
        self.assertEqual([item.code for item in findings], ["WNU201"])
        self.assertIn("Completed 1 of 2", checks[0].reason)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertNotIn("private-value", repr(findings) + repr(checks))

    def test_unavailable_cli_is_safe(self):
        with patch("whynotup.logs.subprocess.Popen", side_effect=OSError("PASSWORD=private-value")):
            findings, checks = collect_log_findings(project(), snapshot())
        self.assertEqual(findings, [])
        self.assertEqual(checks[0].status, "unavailable")
        self.assertNotIn("private-value", repr(checks))

    def test_timeout_kills_and_reaps_process(self):
        class BlockingStream:
            def __init__(self):
                self.event = threading.Event()

            def read(self, count):
                self.event.wait(1)
                return b""

            def close(self):
                pass

        class BlockingProcess(Process):
            def kill(self):
                super().kill()
                self.stdout.event.set()

        process = BlockingProcess()
        process.stdout = BlockingStream()
        with patch("whynotup.logs.subprocess.Popen", return_value=process), patch("whynotup.logs._TIMEOUT", 0.01):
            findings, checks = collect_log_findings(project(), snapshot())
        self.assertEqual(findings, [])
        self.assertTrue(process.killed)
        self.assertEqual(checks[0].status, "unavailable")
        self.assertIn("1 incomplete", checks[0].reason)

    def test_wait_timeout_does_not_keep_partial_output(self):
        process = Process(b"connection refused\n")
        process.wait = lambda timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired("docker", timeout))
        with patch("whynotup.logs.subprocess.Popen", return_value=process):
            findings, checks = collect_log_findings(project(), snapshot())
        self.assertEqual(findings, [])
        self.assertTrue(process.killed)
        self.assertEqual(checks[0].status, "unavailable")


if __name__ == "__main__":
    unittest.main()
