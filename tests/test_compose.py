import os
import tempfile
import unittest
from pathlib import Path

import yaml

from whynotup.compose import ComposeError, load_project
from whynotup.models import SourceLocation


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def load(self, text, dotenv=None, environ=None, **kwargs):
        (self.root / "compose.yaml").write_text(text, encoding="utf-8")
        if dotenv is not None:
            (self.root / ".env").write_text(dotenv, encoding="utf-8")
        return load_project(self.root, environ=environ or {}, **kwargs)

    def test_defaults_required_empty_and_shell_precedence(self):
        p = self.load('''services:
  api:
    image: test
    environment:
      FIRST: ${NAME}
      SECOND: ${EMPTY:-fallback}
      THIRD: ${EMPTY-fallback}
      FOURTH: ${NOPE:-${NAME}}
      REQUIRED: ${REQUIRED:?Please configure this}
      ESCAPED: $$LITERAL
''', "NAME=dotenv\nEMPTY=\n", {"NAME": "shell"})
        values = p.service_environment["api"]
        self.assertEqual(values["FIRST"], "shell")
        self.assertEqual(values["SECOND"], "fallback")
        self.assertEqual(values["THIRD"], "")
        self.assertEqual(values["FOURTH"], "shell")
        self.assertEqual(values["ESCAPED"], "$LITERAL")
        self.assertEqual([(f.code, f.severity) for f in p.findings], [("WNU001", "error")])
        self.assertEqual(p.findings[0].evidence[0].line, 9)

    def test_unbound_plain_variable_is_warning_not_guessed_required(self):
        p = self.load('services:\n  api:\n    image: ${IMAGE}\n')
        self.assertEqual(p.findings[0].severity, "warning")

    def test_absent_default_dotenv_does_not_require_a_native_env_file(self):
        p = self.load('services:\n  api:\n    image: alpine:3.21\n')
        self.assertIsNone(p.interpolation_env_file)

    def test_existing_default_dotenv_is_preserved_for_compose_commands(self):
        p = self.load('services:\n  api:\n    image: ${IMAGE}\n', 'IMAGE=alpine:3.21\n')
        self.assertEqual(p.interpolation_env_file, (self.root / ".env").resolve())
        self.assertEqual(p.services["api"]["image"], "alpine:3.21")

    def test_selected_dotenv_does_not_fall_through_to_default_values(self):
        (self.root / ".env").write_text('IMAGE=alpine:3.21\nDEFAULT_ONLY=present\n', encoding="utf-8")
        selected = self.root / "selected.env"
        selected.write_text('CUSTOM_ONLY=selected\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: ${IMAGE:?required}\n', env_file=Path("selected.env"))
        self.assertEqual(p.interpolation_env_file, selected.resolve())
        self.assertEqual(p.environment["CUSTOM_ONLY"], "selected")
        self.assertNotIn("IMAGE", p.environment)
        self.assertNotIn("DEFAULT_ONLY", p.environment)
        self.assertTrue(any(f.code == "WNU001" and f.severity == "error" for f in p.findings))
        self.assertEqual(p.services["api"]["image"], "")

    def test_implicit_compose_env_files_are_rejected_in_shell_or_dotenv(self):
        configuration = 'services:\n  api:\n    image: alpine:3.21\n'
        with self.assertRaisesRegex(ComposeError, "--env-file") as error:
            self.load(configuration, environ={"COMPOSE_ENV_FILES": "unselected-private-file.env"})
        self.assertNotIn("unselected-private-file", str(error.exception))
        with self.assertRaisesRegex(ComposeError, "--env-file"):
            self.load(configuration, 'COMPOSE_ENV_FILES=another.env\n')

    def test_explicit_dotenv_ignores_implicit_env_file_selection(self):
        selected = self.root / "selected.env"
        selected.write_text('IMAGE=alpine:3.21\nCOMPOSE_ENV_FILES=ignored-from-selected.env\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: ${IMAGE:?required}\n',
                      'IMAGE=wrong-default\n', environ={"COMPOSE_ENV_FILES": "ignored-from-shell.env"},
                      env_file=Path("selected.env"))
        self.assertEqual(p.services["api"]["image"], "alpine:3.21")
        self.assertEqual(p.interpolation_env_file, selected.resolve())
        self.assertTrue(any("COMPOSE_ENV_FILES is not used" in note for note in p.notes))

    def test_compose_disable_env_file_honors_go_boolean_true_spellings(self):
        for value in ("1", "t", "T", "TRUE", "true", "True"):
            with self.subTest(value=value):
                p = self.load('services:\n  api:\n    image: ${IMAGE:?required}\n',
                              'IMAGE=alpine:3.21\n', {"COMPOSE_DISABLE_ENV_FILE": value})
                self.assertIsNone(p.interpolation_env_file)
                self.assertNotIn("IMAGE", p.environment)
                self.assertTrue(any(f.code == "WNU001" for f in p.findings))

    def test_explicit_dotenv_wins_over_disable_default_flag(self):
        selected = self.root / "selected.env"
        selected.write_text('IMAGE=alpine:3.21\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: ${IMAGE:?required}\n',
                      environ={"COMPOSE_DISABLE_ENV_FILE": "true"}, env_file=Path("selected.env"))
        self.assertEqual(p.services["api"]["image"], "alpine:3.21")
        self.assertEqual(p.interpolation_env_file, selected.resolve())

    def test_env_file_is_container_environment_not_interpolation(self):
        (self.root / "api.env").write_text("DB_HOST=db\nPASSWORD='local-test-secret'\nREMOVE=old\n", encoding="utf-8")
        p = self.load('''services:
  api:
    image: test
    env_file: api.env
    environment:
      DB_HOST: override
      REMOVE:
''')
        self.assertEqual(p.service_environment["api"]["DB_HOST"], "override")
        self.assertNotIn("REMOVE", p.service_environment["api"])
        self.assertNotIn("DB_HOST", p.environment)
        self.assertIn("local-test-secret", p.secrets)
        self.assertEqual(p.locations[("service_environment", "api", "PASSWORD")].file, "api.env")
        self.assertEqual(p.locations[("service_environment", "api", "DB_HOST")].line, 6)

    def test_optional_missing_env_file_is_not_failure(self):
        p = self.load('''services:
  api:
    image: test
    env_file:
      - path: optional.env
        required: false
''')
        self.assertEqual(p.findings, [])

    def test_required_missing_env_file(self):
        p = self.load('services:\n  api:\n    image: test\n    env_file: missing.env\n')
        self.assertEqual(p.findings[0].code, "WNU002")
        self.assertEqual(p.findings[0].evidence[0].line, 4)

    def test_dotenv_quotes_comments_and_multiline(self):
        p = self.load('services:\n  api:\n    image: test\n    environment:\n      VALUE: ${VALUE}\n      SINGLE: ${SINGLE}\n      MULTI: ${MULTI}\n',
                      'NAME=world\nVALUE="hello ${NAME}" # comment\nSINGLE=\'${NAME}\'\nMULTI=\'first\nsecond\'\n')
        values = p.service_environment["api"]
        self.assertEqual(values["VALUE"], "hello world")
        self.assertEqual(values["SINGLE"], "${NAME}")
        self.assertEqual(values["MULTI"], "first\nsecond")

    def test_profile_services_are_explicitly_excluded(self):
        p = self.load('services:\n  api:\n    image: test\n  tools:\n    image: test\n    profiles: [debug]\n')
        self.assertEqual(set(p.services), {"api"})
        self.assertTrue(any("excluded" in note for note in p.notes))
        p = load_project(self.root, environ={}, profiles={"debug"})
        self.assertEqual(set(p.services), {"api", "tools"})

    def test_override_never_silently_ignored(self):
        (self.root / "compose.override.yaml").write_text("services: {}\n", encoding="utf-8")
        with self.assertRaises(ComposeError):
            self.load("services:\n  api:\n    image: test\n")
        p = load_project(self.root, file=Path("compose.yaml"), environ={})
        self.assertTrue(any("outside" in note for note in p.notes))

    def test_invalid_yaml_error_does_not_echo_credentials(self):
        with self.assertRaises(ComposeError) as context:
            self.load("services: [\n  password: this-is-a-test-secret\n")
        self.assertNotIn("this-is-a-test-secret", str(context.exception))

    def test_duplicate_keys_rejected_and_yaml_merge_allowed(self):
        with self.assertRaises(ComposeError):
            self.load("services:\n  api:\n    image: one\n    image: two\n")
        p = self.load('x-base: &base\n  image: test\nservices:\n  api:\n    <<: *base\n    environment: {KEY: value}\n')
        self.assertEqual(p.services["api"]["image"], "test")

    def test_recursive_alias_and_unsupported_include_are_rejected(self):
        for text in ('services: &loop\n  api: *loop\n', 'include: extra.yaml\nservices:\n  api:\n    image: test\n'):
            with self.assertRaises(ComposeError):
                self.load(text)

    def test_bool_environment_is_not_silently_changed(self):
        with self.assertRaises(ComposeError):
            self.load('services:\n  api:\n    image: test\n    environment: {FLAG: true}\n')

    def test_compose_yaml_boolean_spellings_preserve_environment_names(self):
        p = self.load('services:\n  api:\n    image: test\n    environment:\n      ON: on\n      OFF: off\n      YES: yes\n      NO: no\n      MIXED: Yes\n')
        self.assertEqual(p.service_environment["api"], {"ON": "on", "OFF": "off", "YES": "yes", "NO": "no", "MIXED": "Yes"})
        self.assertIs(yaml.safe_load("on"), True)  # external PyYAML callers are unchanged

    def test_raw_env_file_keeps_dollars_and_quotes(self):
        (self.root / "raw.env").write_text('VALUE="${NOPE}"\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file:\n      - path: raw.env\n        format: raw\n')
        self.assertEqual(p.service_environment["api"]["VALUE"], '"${NOPE}"')
        self.assertEqual(p.findings, [])

    def test_interpolated_profiles_are_selected_and_raw_services_preserved(self):
        p = self.load('services:\n  api:\n    image: test\n    profiles: ["${FEATURE:-dev}"]\n  unused:\n    image: test\n    profiles: [other]\n', profiles={"dev"})
        self.assertEqual(set(p.services), {"api"})
        self.assertIn("unused", p.raw_services)
        self.assertEqual(p.active_profiles, ("dev",))

    def test_even_backslashes_before_dotenv_closing_quote(self):
        p = self.load('services:\n  api:\n    image: test\n    environment: {VALUE: "${VALUE}"}\n', 'VALUE="path\\\\"\n')
        self.assertEqual(p.service_environment["api"]["VALUE"], "path\\")

    def test_compose_file_environment_requires_explicit_scope(self):
        with self.assertRaises(ComposeError):
            self.load('services:\n  api:\n    image: test\n', environ={"COMPOSE_FILE": "compose.yaml:production.yaml"})
        p = load_project(self.root, file=Path("compose.yaml"), environ={"COMPOSE_FILE": "compose.yaml:production.yaml"})
        self.assertTrue(any("COMPOSE_FILE" in note for note in p.notes))

    def test_simple_interpolation_evidence_points_to_dotenv_unless_shell_wins(self):
        self.load('services:\n  api:\n    image: test\n    environment: {DB_HOST: "${DB_HOST}"}\n', 'DB_HOST=localhost\n')
        p = load_project(self.root, environ={})
        self.assertEqual(p.locations[("service_environment", "api", "DB_HOST")], SourceLocation('.env', 1))
        p = load_project(self.root, environ={"DB_HOST": "db"})
        self.assertEqual(p.locations[("service_environment", "api", "DB_HOST")].file, "compose.yaml")

    def test_dotenv_comment_boundary_matches_compose(self):
        (self.root / "values.env").write_text('EMPTY=\nHASH= # comment\nINLINE=value # comment\nADJACENT=value#literal\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file: values.env\n')
        self.assertEqual(p.service_environment["api"], {"EMPTY": "", "HASH": "# comment", "INLINE": "value", "ADJACENT": "value#literal"})

    def test_multiline_quote_preserves_first_line_trailing_whitespace(self):
        p = self.load('services:\n  api:\n    image: test\n    environment: {VALUE: "${VALUE}"}\n', "VALUE='first   \nsecond'\n")
        self.assertEqual(p.service_environment["api"]["VALUE"], "first   \nsecond")

    def test_double_quoted_dotenv_control_escapes_and_literal_dollar(self):
        (self.root / "values.env").write_text(r'''LITERAL="\$NAME"
EXPANDED="\\$NAME"
CONTROLS="\a\b\f\n\r\t\v"
UNKNOWN="\q\u0041"
''', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file: values.env\n', environ={"NAME": "fixture"})
        self.assertEqual(p.service_environment["api"], {"LITERAL": "$NAME", "EXPANDED": "\\fixture", "CONTROLS": "\a\b\f\n\r\t\v", "UNKNOWN": r"\q\u0041"})
        self.assertEqual(p.findings, [])

    def test_raw_env_file_preserves_all_value_whitespace(self):
        (self.root / "raw.env").write_text('VALUE=  data  \nEMPTY=   \nHASH= # literal\nQUOTED="${LITERAL}"\n', encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file:\n      - path: raw.env\n        format: raw\n')
        self.assertEqual(p.service_environment["api"], {"VALUE": "  data  ", "EMPTY": "   ", "HASH": " # literal", "QUOTED": '"${LITERAL}"'})
        self.assertEqual(p.findings, [])

    def test_raw_env_file_does_not_accept_export_or_trailing_key_whitespace(self):
        for text in ["export VALUE=data\n", "VALUE =data\n"]:
            with self.subTest(text=text):
                (self.root / "raw.env").write_text(text, encoding="utf-8")
                with self.assertRaises(ComposeError):
                    self.load('services:\n  api:\n    image: test\n    env_file:\n      - path: raw.env\n        format: raw\n')

    def test_later_env_file_can_reference_earlier_files_but_shell_wins(self):
        (self.root / "first.env").write_text("FIRST=seed\nSHARED=first\n", encoding="utf-8")
        (self.root / "second.env").write_text("SHARED=second\nREFERENCE=$FIRST\nLOCAL=$SHARED\n", encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file: [first.env, second.env]\n')
        self.assertEqual(p.service_environment["api"], {"FIRST": "seed", "SHARED": "second", "REFERENCE": "seed", "LOCAL": "second"})
        p = load_project(self.root, environ={"SHARED": "shell"})
        self.assertEqual(p.service_environment["api"]["SHARED"], "second")
        self.assertEqual(p.service_environment["api"]["LOCAL"], "shell")
        self.assertEqual(p.findings, [])

    def test_bare_env_file_keys_resolve_lookup_or_remain_unset(self):
        (self.root / "first.env").write_text("PREVIOUS_FILE=first\n", encoding="utf-8")
        (self.root / "second.env").write_text("PREVIOUS_FILE\nPREVIOUS_LINE=second\nPREVIOUS_LINE\nSHELL_VALUE\nUNKNOWN\n", encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file: [first.env, second.env]\n', environ={"SHELL_VALUE": "shell"})
        self.assertEqual(p.service_environment["api"], {"PREVIOUS_FILE": "first", "PREVIOUS_LINE": "second", "SHELL_VALUE": "shell"})

    def test_raw_bare_key_resolves_lookup_without_interpolating_values(self):
        (self.root / "raw.env").write_text("SHELL_VALUE\nLITERAL=$SHELL_VALUE\n", encoding="utf-8")
        p = self.load('services:\n  api:\n    image: test\n    env_file:\n      - path: raw.env\n        format: raw\n', environ={"SHELL_VALUE": "shell"})
        self.assertEqual(p.service_environment["api"], {"SHELL_VALUE": "shell", "LITERAL": "$SHELL_VALUE"})


if __name__ == "__main__":
    unittest.main()
