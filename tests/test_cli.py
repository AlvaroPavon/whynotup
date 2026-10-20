import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whynotup.cli import main
from whynotup.models import Check, RuntimeSnapshot


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.compose = self.root / "compose.yaml"
        self.compose.write_text("services:\n  api:\n    image: example/api\n", encoding="utf-8")

    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(list(args))
        return code, output.getvalue(), errors.getvalue()

    def test_clean_project_discloses_skipped_runtime(self):
        code, output, errors = self.run_cli("doctor", str(self.root), "--format", "json")
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["findings"], [])
        self.assertTrue(any(c["name"] == "docker-runtime" and c["status"] == "skipped" for c in data["checks"]))
        self.assertEqual(errors, "")

    def test_demo_is_useful_and_does_not_leak_synthetic_secret(self):
        for format in ("text", "markdown", "json"):
            code, output, errors = self.run_cli("demo", "--format", format)
            self.assertEqual(code, 1)
            self.assertIn("WNU003", output)
            self.assertIn("WNU005", output)
            self.assertNotIn("demo_password_do_not_use", output)
            self.assertEqual(errors, "")
            if format == "json":
                self.assertEqual(json.loads(output)["project"], "bundled-demo")

    def test_failure_thresholds_and_no_unintended_file_writes(self):
        self.compose.write_text("services:\n  api:\n    image: ${MISSING}\n", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(self.run_cli("doctor", str(self.root))[0], 0)
        self.assertEqual(self.run_cli("doctor", str(self.root), "--fail-on", "warning")[0], 1)
        self.assertEqual(self.run_cli("doctor", str(self.root), "--fail-on", "none")[0], 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_report_is_only_explicit_write_and_never_clobbers_config(self):
        target = self.root / "report.json"
        code, stdout, stderr = self.run_cli("doctor", str(self.root), "--format", "json", "--output", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(target.read_text())["schema_version"], "1.0")
        original = self.compose.read_bytes()
        self.assertEqual(self.run_cli("doctor", str(self.root), "--output", str(self.compose))[0], 2)
        self.assertEqual(self.compose.read_bytes(), original)
        self.assertEqual(self.run_cli("doctor", str(self.root), "--output", str(target))[0], 2)

    def test_invalid_yaml_does_not_leak_source_or_print_traceback(self):
        self.compose.write_text("services: [password: synthetic-secret-value\n", encoding="utf-8")
        code, stdout, stderr = self.run_cli("doctor", str(self.root))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("synthetic-secret-value", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_service_filter_and_invalid_service(self):
        self.compose.write_text('services:\n  api:\n    image: ${ONE}\n  web:\n    image: ${TWO}\n', encoding="utf-8")
        code, output, _ = self.run_cli("doctor", str(self.root), "--service", "api", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual({f["service"] for f in json.loads(output)["findings"]}, {"api"})
        self.assertEqual(self.run_cli("doctor", str(self.root), "--service", "absent")[0], 2)

    def test_requested_unavailable_runtime_is_not_clean_and_cannot_be_ignored(self):
        snapshot = RuntimeSnapshot(False, reason="unavailable", checks=[Check("docker-runtime", "unavailable", "unavailable")])
        with patch("whynotup.cli.collect_runtime", return_value=snapshot), patch("whynotup.cli.check_ports", return_value=([], [])):
            for threshold in ("error", "none"):
                code, output, _ = self.run_cli("doctor", str(self.root), "--runtime", "--format", "json", "--fail-on", threshold)
                self.assertEqual(code, 3)
                self.assertTrue(any(c["status"] == "unavailable" for c in json.loads(output)["checks"]))

    def test_demo_does_not_depend_on_shell_configuration(self):
        with patch.dict(os.environ, {"APP_SECRET": "provided", "COMPOSE_PROFILES": "excluded", "DB_HOST": "example"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["demo", "--format", "json"])
        self.assertEqual(code, 1)
        codes = {f["code"] for f in json.loads(output.getvalue())["findings"]}
        self.assertTrue({"WNU001", "WNU003", "WNU005"}.issubset(codes))

    def test_inactive_dependency_is_not_misreported_as_undefined(self):
        self.compose.write_text('services:\n  api:\n    image: test\n    depends_on: [db]\n  db:\n    image: postgres:16\n    profiles: [data]\n', encoding="utf-8")
        code, output, _ = self.run_cli("doctor", str(self.root), "--format", "json")
        self.assertEqual(code, 1)
        finding = next(f for f in json.loads(output)["findings"] if f["code"] == "WNU008")
        self.assertIn("inactive", finding["title"].lower())

    def test_review_is_offline_deterministic_and_uses_warning_threshold(self):
        self.compose.write_text("services:\n  api:\n    image: alpine:latest\n", encoding="utf-8")
        with patch("subprocess.run", side_effect=AssertionError("Offline review must not execute Docker")):
            first = self.run_cli("review", str(self.root), "--format", "json")
            second = self.run_cli("review", str(self.root), "--format", "json")
            strict = self.run_cli("review", str(self.root), "--fail-on", "warning")
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0)
        self.assertEqual(strict[0], 1)
        report = json.loads(first[1])
        self.assertIn("WNU101", {f["code"] for f in report["findings"]})
        self.assertTrue(any(c["name"] == "best-practices" and c["status"] == "completed" for c in report["checks"]))

    def test_review_target_does_not_activate_compose_profiles(self):
        self.compose.write_text("services:\n  api:\n    image: alpine:latest\n  database:\n    image: postgres:latest\n    profiles: [production]\n", encoding="utf-8")
        code, output, _ = self.run_cli("review", str(self.root), "--target", "production", "--format", "json")
        self.assertEqual(code, 0)
        self.assertNotIn("database", {f["service"] for f in json.loads(output)["findings"]})

    def test_all_runs_each_requested_component_once(self):
        snapshot = RuntimeSnapshot(True, [{"id": "a" * 64, "service": "api"}], checks=[Check("docker-runtime", "completed")])
        with patch("whynotup.cli.validate_compose", return_value=([], [Check("docker-compose-validation", "completed")])) as native, \
             patch("whynotup.cli.collect_runtime", return_value=snapshot) as runtime, \
             patch("whynotup.cli.check_ports", return_value=([], [])), \
             patch("whynotup.cli.collect_log_findings", return_value=([], [Check("docker-logs", "completed")])) as logs:
            code, output, _ = self.run_cli("doctor", str(self.root), "--all", "--format", "json")
        self.assertEqual(code, 0)
        native.assert_called_once()
        runtime.assert_called_once()
        logs.assert_called_once()
        self.assertIn("WNU101", {f["code"] for f in json.loads(output)["findings"]})

    def test_logs_imply_runtime_and_filter_before_collection(self):
        self.compose.write_text("services:\n  api:\n    image: example/api\n  db:\n    image: postgres:16\n", encoding="utf-8")
        snapshot = RuntimeSnapshot(True, [
            {"id": "a" * 64, "service": "api"}, {"id": "b" * 64, "service": "db"},
            {"id": "c" * 64, "service": "unrelated"},
        ], checks=[Check("docker-runtime", "completed")])
        with patch("whynotup.cli.collect_runtime", return_value=snapshot) as runtime, \
             patch("whynotup.cli.check_ports", return_value=([], [])), \
             patch("whynotup.cli.collect_log_findings", return_value=([], [Check("docker-logs", "completed")])) as logs:
            code, _, _ = self.run_cli("doctor", str(self.root), "--logs", "--service", "api", "--log-tail", "25")
        self.assertEqual(code, 0)
        runtime.assert_called_once()
        selected = logs.call_args.args[1]
        self.assertEqual([c["service"] for c in selected.containers], ["api"])
        self.assertEqual(logs.call_args.kwargs, {"tail": 25})
        self.assertEqual(len(snapshot.containers), 3)

    def test_unavailable_requested_validation_keeps_exit_three(self):
        with patch("whynotup.cli.validate_compose", return_value=([], [Check("docker-compose-validation", "unavailable", "Docker absent")])):
            code, output, _ = self.run_cli("doctor", str(self.root), "--validate", "--fail-on", "none", "--format", "json")
        self.assertEqual(code, 3)
        self.assertIn("unavailable", output)

    def test_existing_output_is_rejected_before_optional_docker_reads(self):
        output = self.root / "existing.json"
        output.write_text("preserve me", encoding="utf-8")
        with patch("whynotup.cli.validate_compose") as native, patch("whynotup.cli.collect_runtime") as runtime:
            self.assertEqual(self.run_cli("doctor", str(self.root), "--all", "--output", str(output))[0], 2)
        native.assert_not_called()
        runtime.assert_not_called()
        self.assertEqual(output.read_text(), "preserve me")

    def test_invalid_log_tail_is_an_input_error(self):
        for value in ("0", "1001", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                self.run_cli("doctor", str(self.root), "--logs", "--log-tail", value)
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
