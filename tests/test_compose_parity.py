"""Optional parser parity against Docker Compose, using synthetic fixtures only.

These commands parse config; they never contact the daemon or start containers.
Pure Python regressions live in test_compose.py so Docker is not required by CI.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from whynotup.compose import load_project


class ComposeParserParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Do not pass the process environment or any configured credentials to
        # the external parser. PATH is needed only to locate the Docker CLI.
        cls.command_environment = {"PATH": os.environ.get("PATH", "")}
        try:
            result = subprocess.run(
                ["docker", "compose", "version", "--short"],
                env=cls.command_environment, capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("Docker Compose parser is not available.")
        if result.returncode:
            raise unittest.SkipTest("Docker Compose parser is not available.")
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
        cls.compose_version = tuple(map(int, match.groups())) if match else (0, 0, 0)

    def compare(self, service, *, files=None, dotenv=None, environ=None, raw=False, source=None):
        if raw and self.compose_version < (2, 30, 0):
            self.skipTest("Raw env_file format requires Docker Compose 2.30 or newer.")
        with tempfile.TemporaryDirectory(prefix="whynotup-parity-") as directory:
            root = Path(directory)
            for name, contents in (files or {}).items():
                (root / name).write_text(contents, encoding="utf-8")
            if dotenv is not None:
                (root / ".env").write_text(dotenv, encoding="utf-8")
            document = {"services": {"api": {"image": "alpine:3.20", **service}}}
            compose = root / "compose.yaml"
            compose.write_text(source if source is not None else yaml.safe_dump(document), encoding="utf-8")
            fixture_environment = dict(environ or {})
            result = subprocess.run(
                ["docker", "compose", "--project-name", "whynotup-parity", "-f", str(compose),
                 "config", "--format", "json"],
                cwd=root, env={**self.command_environment, **fixture_environment},
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[:1000])
            self.assertLess(len(result.stdout), 1024 * 1024)
            expected = json.loads(result.stdout)["services"]["api"].get("environment", {})
            # Compose escapes every literal dollar when rendering config so it
            # can be loaded again. Undo that serialization escape for comparison
            # with WhyNotUp's effective, in-container environment values.
            expected = {key: value.replace("$$", "$") for key, value in expected.items() if value is not None}
            actual = load_project(root, environ=fixture_environment)
            self.assertEqual(actual.service_environment["api"], expected)
            return actual

    def test_comment_boundaries_and_empty_values(self):
        self.compare({"env_file": "values.env"}, files={"values.env": '''EMPTY=
HASH= # literal hash
INLINE=value # comment
ADJACENT=value#literal
QUOTED="value"#comment
'''})

    def test_yaml_boolean_like_strings_and_environment_names(self):
        self.compare({}, source='services:\n  api:\n    image: alpine:3.20\n    environment:\n      ON: on\n      OFF: off\n      YES: yes\n      NO: no\n      MIXED: Yes\n')

    def test_quoted_escapes_literals_and_multiline_whitespace(self):
        self.compare({"env_file": "values.env"}, environ={"NAME": "fixture"}, files={"values.env": r'''LITERAL="\$NAME"
EXPANDED="\\$NAME"
CONTROLS="\a\b\f\n\r\t\v"
UNKNOWN="\q\u0041"
SINGLE='${NAME}'
MULTILINE='first[padding]
second'
'''.replace('[padding]', ' ' * 3)})

    def test_raw_format_retains_whitespace_quotes_and_dollars(self):
        self.compare(
            {"env_file": [{"path": "raw.env", "format": "raw"}]}, raw=True,
            files={"raw.env": 'VALUE=  data  \nEMPTY=   \nHASH= # literal\nQUOTED="${NAME}"\nDOLLARS=$$NAME\nSHELL_VALUE\n'},
            environ={"SHELL_VALUE": "fixture"},
        )

    def test_later_files_reference_earlier_values(self):
        self.compare({"env_file": ["first.env", "second.env"]}, files={
            "first.env": "FIRST=seed\nSHARED=first\n",
            "second.env": "SHARED=second\nREFERENCE=$FIRST\nLOCAL=$SHARED\n",
        })

    def test_interpolation_environment_has_priority_over_file_local_values(self):
        self.compare({"env_file": ["first.env", "second.env"]}, files={
            "first.env": "FIRST=seed\nSHARED=first\n",
            "second.env": "SHARED=second\nREFERENCE=$FIRST\nLOCAL=$SHARED\n",
        }, environ={"SHARED": "shell"})

    def test_bare_keys_resolve_prior_files_prior_lines_and_shell(self):
        self.compare({"env_file": ["first.env", "second.env"]}, files={
            "first.env": "PREVIOUS_FILE=first\n",
            "second.env": "PREVIOUS_FILE\nPREVIOUS_LINE=second\nPREVIOUS_LINE\nSHELL_VALUE\nUNKNOWN\n",
        }, environ={"SHELL_VALUE": "shell"})

    def test_explicit_environment_overrides_service_env_files(self):
        self.compare({"env_file": "values.env", "environment": {"OVERRIDE": "declared", "REMOVE": None, "PASS": None}},
                     files={"values.env": "OVERRIDE=file\nREMOVE=file\nPASS=file\n"}, environ={"PASS": "shell"})

    def test_list_environment_and_dotenv_interpolation(self):
        self.compare({"env_file": "values.env", "environment": ["OVERRIDE=${NAME}", "REMOVE", "PASS", "LITERAL=$$NAME"]},
                     files={"values.env": "OVERRIDE=file\nREMOVE=file\nPASS=file\n"},
                     dotenv="NAME=dotenv\nPASS=dotenv-pass\n", environ={"NAME": "shell"})

    def test_nested_defaults_and_empty_vs_unset(self):
        self.compare({"environment": {
            "PLAIN": "$NAME", "EMPTY_DEFAULT": "${EMPTY:-fallback}",
            "EMPTY_SET": "${EMPTY-fallback}", "NESTED": "${ABSENT:-${NAME}}",
            "ALTERNATE": "${NAME:+yes}", "EMPTY_ALTERNATE": "${EMPTY+yes}",
            "LITERAL": "$$NAME",
        }}, dotenv="NAME=fixture\nEMPTY=\n")


if __name__ == "__main__":
    unittest.main()
