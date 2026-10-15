import json
import unittest

from whynotup.models import Check, Evidence, Finding, Report, Suggestion
from whynotup.redaction import Redactor
from whynotup.reporting import render


class RedactionTests(unittest.TestCase):
    def test_known_values_url_bearer_and_assignments(self):
        raw = "postgres://alice:sensitive-pass@db:5432/app Bearer ABCDEFGHIJKL PASSWORD=other-secret"
        result = Redactor({"sensitive-pass"}).text(raw)
        for value in ("alice", "sensitive-pass", "ABCDEFGHIJKL", "other-secret"):
            self.assertNotIn(value, result)
        self.assertIn("db:5432", result)

    def test_same_secret_consistent_across_report_and_formats(self):
        secret = "synthetic-top-secret-value"
        finding = Finding("WNU005", "warning", "Check this URI", "Review connection", "api",
                          evidence=[Evidence(f"postgres://user:{secret}@localhost/app")],
                          suggestion=Suggestion("Review", before=f"postgres://user:{secret}@localhost/app", after=f"postgres://user:{secret}@db/app"))
        report = Report("test", "compose.yaml", [finding], [Check("runtime", "skipped")])
        for fmt in ("text", "markdown", "json"):
            result = render(report, format=fmt, redactor=Redactor({secret}))
            self.assertNotIn(secret, result)
            self.assertNotIn("user:", result)
            if fmt == "json":
                self.assertEqual(json.loads(result)["schema_version"], "1.0")

    def test_private_key_and_common_provider_token(self):
        value = "-----BEGIN PRIVATE KEY-----\nABC123\n-----END PRIVATE KEY----- ghp_" + "a" * 36
        result = Redactor().text(value)
        self.assertNotIn("ABC123", result)
        self.assertNotIn("ghp_", result)

    def test_short_known_secret_does_not_destroy_codes(self):
        result = Redactor({"1"}).text("WNU001 line 1 port 15432")
        self.assertIn("WNU001", result)
        self.assertIn("15432", result)
        self.assertNotIn("line 1 ", result)
