"""Real Compose configuration validation; deliberately no reachable daemon."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from whynotup.compose import load_project
from whynotup.native import validate_compose


@unittest.skipUnless(os.environ.get("WHYNOTUP_DOCKER_TESTS") == "1", "Set WHYNOTUP_DOCKER_TESTS=1 to exercise the installed Docker Compose CLI.")
class NativeComposeEnvironmentTests(unittest.TestCase):
    def test_selected_empty_env_file_does_not_reload_filled_default(self):
        with tempfile.TemporaryDirectory(prefix="whynotup-native-env-") as directory:
            base = Path(directory)
            variable = "WNU_NATIVE_REQUIRED_" + uuid.uuid4().hex.upper()
            file = base / "compose.yaml"
            file.write_text("services:\n  api:\n    image: \"alpine:${" + variable + ":?set the value}\"\n", encoding="utf-8")
            (base / ".env").write_text(variable + "=3.21\n", encoding="utf-8")
            selected = base / "selected.env"
            selected.write_text("", encoding="utf-8")
            environment = {
                **os.environ, "DOCKER_CONTEXT": "",
                "DOCKER_HOST": "unix://" + str(base / "absent-daemon.sock"),
                "COMPOSE_PROJECT_NAME": "whynotup-it-" + uuid.uuid4().hex,
            }
            default = load_project(file, environ=environment)
            default_findings, default_checks = validate_compose(default)
            self.assertEqual(default_findings, [])
            self.assertEqual(default_checks[0].status, "passed", default_checks[0].reason)

            custom = load_project(file, env_file=selected, environ=environment)
            self.assertEqual(custom.interpolation_env_file, selected.resolve())
            self.assertTrue(any(finding.code == "WNU001" for finding in custom.findings))
            findings, checks = validate_compose(custom)
            self.assertEqual([finding.code for finding in findings], ["WNU010"])
            self.assertEqual(checks[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
