import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whynotup.action import main
from whynotup.compose import ComposeError


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "compose.yaml"
        self.config.write_text("services:\n  api:\n    image: test\n", encoding="utf-8")
        self.summary, self.outputs = self.root / "summary.md", self.root / "outputs.txt"

    def run_action(self, **values):
        environment = {"WNU_INPUT_PATH": str(self.root), "GITHUB_STEP_SUMMARY": str(self.summary), "GITHUB_OUTPUT": str(self.outputs), **values}
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environment, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main()
        return code, output.getvalue(), errors.getvalue()

    def test_clean_action_writes_summary_and_numeric_outputs(self):
        before = self.config.read_bytes()
        code, output, errors = self.run_action()
        self.assertEqual(code, 0)
        self.assertIn("No findings in the completed checks", self.summary.read_text(encoding="utf-8"))
        self.assertIn("finding-count=0", self.outputs.read_text(encoding="utf-8"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn("::stop-commands::", output)
        self.assertEqual(errors, "")

    def test_error_finding_is_redacted_and_fails_the_action(self):
        self.config.write_text('services:\n  api:\n    image: "${MISSING:?required}"\n    environment:\n      DB_PASSWORD: synthetic-password\n', encoding="utf-8")
        code, output, errors = self.run_action()
        self.assertEqual(code, 1)
        self.assertIn("WNU001", self.summary.read_text(encoding="utf-8"))
        self.assertNotIn("synthetic-password", output)
        self.assertIn("exit-code=1", self.outputs.read_text(encoding="utf-8"))
        self.assertEqual(errors, "")

    def test_invalid_action_inputs_are_not_executed(self):
        for values in ({"WNU_INPUT_RUNTIME": "maybe"}, {"WNU_INPUT_FAIL_ON": "invalid"}, {"WNU_INPUT_PROFILES": "$(touch hacked)"}):
            with self.subTest(values=values):
                code, _, errors = self.run_action(**values)
                self.assertEqual(code, 2)
                self.assertNotIn("$(touch", errors)
                self.assertFalse((self.root / "hacked").exists())

    def test_profile_selection_and_warning_threshold(self):
        self.config.write_text('services:\n  api:\n    image: "${MISSING}"\n    profiles: [development]\n', encoding="utf-8")
        code, _, _ = self.run_action(WNU_INPUT_PROFILES="development", WNU_INPUT_FAIL_ON="warning")
        self.assertEqual(code, 1)
        self.assertIn("warning-count=1", self.outputs.read_text(encoding="utf-8"))

    def test_compose_content_cannot_enable_workflow_commands(self):
        self.config.write_text('services:\n  "api\\n::error::synthetic-command":\n    image: "${MISSING}"\n', encoding="utf-8")
        _, output, _ = self.run_action()
        lines = output.splitlines()
        marker = lines[0].partition("::stop-commands::")[2]
        self.assertTrue(marker)
        self.assertEqual(lines[-1], f"::{marker}::")
        self.assertNotIn(f"::{marker}::", "\n".join(lines[1:-1]))

    def test_input_errors_cannot_inject_commands_through_stderr(self):
        message = "Invalid dotenv key in bad\n::warning::UNTRUSTED_WORKFLOW_COMMAND.env at line 1."
        with patch("whynotup.action._doctor", side_effect=ComposeError(message)):
            code, output, errors = self.run_action()
        self.assertEqual(code, 2)
        self.assertNotIn("UNTRUSTED_WORKFLOW_COMMAND", errors)
        lines = output.splitlines()
        marker = lines[0].partition("::stop-commands::")[2]
        self.assertTrue(marker)
        self.assertEqual(lines[-1], f"::{marker}::")
        self.assertIn("UNTRUSTED_WORKFLOW_COMMAND", "\n".join(lines[1:-1]))
