from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from whynotup.diagnostics import diagnose
from whynotup.models import ComposeProject, SourceLocation


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def project(self, services, environment=None, raw_services=None, locations=None):
        return ComposeProject(
            self.root, self.root / "compose.yaml", services, raw_services or services,
            {}, environment or {}, locations or {},
        )

    def codes(self, project):
        return [finding.code for finding in diagnose(project)]

    def database(self, api=None, db=None, environment=None):
        return self.project(
            {"api": api or {"depends_on": ["db"]},
             "db": db or {"image": "postgres:16", "ports": ["55432:5432"]}},
            {"api": environment or {"DB_HOST": "localhost", "DB_PORT": "55432"}},
        )

    def test_missing_short_bind_warns_without_claiming_startup_failure(self):
        project = self.project({"api": {"volumes": ["./settings.ini:/app/settings.ini:ro"]}})
        finding, = diagnose(project)
        self.assertEqual((finding.code, finding.severity), ("WNU003", "warning"))
        self.assertIn("create a directory", finding.explanation)
        self.assertFalse((self.root / "settings.ini").exists())

    def test_non_creating_bind_is_error_and_has_source_line(self):
        project = self.project(
            {"api": {"volumes": [{"type": "bind", "source": "./missing", "target": "/data", "bind": {"create_host_path": False}}]}},
            locations={("services", "api", "volumes", 0, "source"): SourceLocation("compose.yaml", 12)},
        )
        finding, = diagnose(project)
        self.assertEqual(finding.severity, "error")
        self.assertEqual(finding.evidence[0].line, 12)
        self.assertIn("remote Docker", finding.explanation)

    def test_named_anonymous_and_existing_mounts_are_valid(self):
        (self.root / "existing").mkdir()
        project = self.project({"api": {"volumes": [
            "cache:/data", "/anonymous", "/anonymous:ro", "./existing:/existing",
            {"type": "volume", "source": "cache", "target": "/cache"},
        ]}})
        self.assertEqual(diagnose(project), [])

    def test_unresolved_bind_is_left_to_loader(self):
        project = self.project({"api": {"volumes": ["${MOUNT_SOURCE}:/data"]}})
        self.assertEqual(diagnose(project), [])

    def test_loopback_and_published_port_each_explain_evidence(self):
        findings = diagnose(self.database())
        self.assertEqual([finding.code for finding in findings], ["WNU005", "WNU006"])
        self.assertTrue(all(finding.confidence == "probable" for finding in findings))
        self.assertEqual(findings[0].suggestion.after, "db")
        self.assertEqual(findings[1].suggestion.after, "5432")

    def test_correct_container_hostname_and_target_port_pass(self):
        project = self.database(environment={"DB_HOST": "db", "DB_PORT": "5432"})
        self.assertEqual(diagnose(project), [])

    def test_published_port_with_correct_hostname_is_detected(self):
        findings = diagnose(self.database(environment={"DB_HOST": "db", "DB_PORT": "55432"}))
        self.assertEqual([finding.code for finding in findings], ["WNU006"])

    def test_loopback_without_identifiable_peer_is_not_speculated_about(self):
        project = self.project({"api": {}}, {"api": {"DB_HOST": "localhost"}})
        self.assertEqual(diagnose(project), [])

    def test_ambiguous_database_peers_are_not_guessed(self):
        project = self.project(
            {"api": {"depends_on": ["postgres", "mysql"]}, "postgres": {"image": "postgres:16"}, "mysql": {"image": "mysql:8"}},
            {"api": {"DB_HOST": "localhost"}},
        )
        self.assertEqual(diagnose(project), [])

    def test_scheme_disambiguates_peer_without_printing_credentials(self):
        project = self.project(
            {"api": {"depends_on": ["postgres", "mysql"]}, "postgres": {"image": "postgres:16", "ports": ["55432:5432"]}, "mysql": {"image": "mysql:8"}},
            {"api": {"DATABASE_URL": "postgresql://alice:supersecret@localhost:55432/app"}},
        )
        findings = diagnose(project)
        self.assertEqual([finding.code for finding in findings], ["WNU005", "WNU006"])
        self.assertNotIn("supersecret", repr(findings))
        self.assertEqual(findings[0].suggestion.after, "postgres")

    def test_listen_host_is_not_treated_as_connection_host(self):
        self.assertEqual(diagnose(self.database(environment={"HOST": "localhost", "PORT": "55432"})), [])

    def test_host_network_is_valid_for_host_published_port(self):
        project = self.database(api={"network_mode": "host", "depends_on": ["db"]})
        self.assertEqual(diagnose(project), [])

    def test_shared_database_namespace_accepts_loopback(self):
        project = self.database(api={"network_mode": "service:db", "depends_on": ["db"]}, environment={"PGHOST": "127.0.0.1", "PGPORT": "5432"})
        self.assertEqual(diagnose(project), [])

    def test_transitively_shared_namespace_accepts_loopback(self):
        project = self.project(
            {"api": {"network_mode": "service:sidecar", "depends_on": ["db"]}, "sidecar": {"network_mode": "service:db"}, "db": {"image": "postgres:16"}},
            {"api": {"PGHOST": "::1"}},
        )
        self.assertEqual(diagnose(project), [])

    def test_unknown_external_namespace_is_not_guessed(self):
        project = self.database(api={"network_mode": "container:external", "depends_on": ["db"]})
        self.assertEqual(diagnose(project), [])

    def test_host_network_database_does_not_get_dns_fix(self):
        project = self.database(db={"network_mode": "host", "image": "postgres:16"})
        self.assertEqual(diagnose(project), [])

    def test_distinct_networks_require_connection_before_dns_change(self):
        project = self.database(
            api={"networks": ["front"], "depends_on": ["db"]},
            db={"image": "postgres:16", "networks": ["back"], "ports": ["55432:5432"]},
        )
        finding, = diagnose(project)
        self.assertEqual(finding.code, "WNU005")
        self.assertIn("common Compose network", finding.suggestion.description)
        self.assertIsNone(finding.suggestion.after)

    def test_named_peer_convention_can_identify_custom_service(self):
        project = self.project({"web": {"depends_on": ["api"]}, "api": {"image": "custom/api"}}, {"web": {"API_HOST": "localhost"}})
        self.assertEqual(self.codes(project), ["WNU005"])

    def test_named_peer_url_identifies_custom_service(self):
        project = self.project({"web": {"depends_on": ["api"]}, "api": {"image": "custom/api"}}, {"web": {"API_URL": "http://localhost:8080"}})
        self.assertEqual(self.codes(project), ["WNU005"])

    def test_database_sharing_other_namespace_suggests_endpoint_owner(self):
        project = self.project(
            {"api": {"depends_on": ["db"]}, "db": {"image": "postgres:16", "network_mode": "service:sidecar"}, "sidecar": {}},
            {"api": {"PGHOST": "localhost"}},
        )
        finding, = diagnose(project)
        self.assertEqual(finding.suggestion.after, "sidecar")

    def test_shared_namespace_service_name_is_not_assumed_to_be_dns_alias(self):
        project = self.project(
            {"api": {}, "db": {"image": "postgres:16", "network_mode": "service:sidecar", "ports": ["55432:5432"]}, "sidecar": {}},
            {"api": {"PGHOST": "db", "PGPORT": "55432"}},
        )
        self.assertEqual(diagnose(project), [])

    def test_pgport_location_can_point_to_service_env_file(self):
        project = self.database(environment={"PGHOST": "db", "PGPORT": "55432"})
        project.locations[("service_environment", "api", "PGPORT")] = SourceLocation("config/api.env", 7)
        finding, = diagnose(project)
        self.assertEqual((finding.evidence[0].file, finding.evidence[0].line), ("config/api.env", 7))
        self.assertEqual(finding.suggestion.file, "config/api.env")

    def test_container_alias_and_long_ports_are_supported(self):
        project = self.database(
            db={"image": "postgres:16", "networks": {"default": {"aliases": ["database.internal"]}}, "ports": [{"published": "55432", "target": 5432}]},
            environment={"DB_HOST": "database.internal", "DB_PORT": "55432"},
        )
        self.assertEqual(self.codes(project), ["WNU006"])

    def test_ambiguous_dns_aliases_are_not_guessed(self):
        services = {"api": {}}
        for name, port in [("one", "5432"), ("two", "6432")]:
            services[name] = {"networks": {"default": {"aliases": ["db"]}}, "ports": [f"55432:{port}"]}
        project = self.project(services, {"api": {"DB_HOST": "db", "DB_PORT": "55432"}})
        self.assertEqual(diagnose(project), [])

    def test_bracketed_ipv6_publication_supports_port_comparison(self):
        project = self.database(db={"image": "postgres:16", "ports": ["[::1]:55432:5432"]}, environment={"DB_HOST": "db", "DB_PORT": "55432"})
        self.assertEqual(self.codes(project), ["WNU006"])

    def test_udp_and_ambiguous_target_mappings_do_not_get_tcp_fix(self):
        for ports in [["55432:5432/udp"], ["127.0.0.1:55432:5432", "127.0.0.2:55432:6432"]]:
            with self.subTest(ports=ports):
                project = self.database(db={"image": "postgres:16", "ports": ports}, environment={"DB_HOST": "db", "DB_PORT": "55432"})
                self.assertEqual(diagnose(project), [])

    def test_port_that_is_also_a_target_or_exposed_is_valid(self):
        for settings in [{"ports": ["55432:5432", "65432:55432"]}, {"ports": ["55432:5432"], "expose": [55432]}]:
            with self.subTest(settings=settings):
                project = self.database(db={"image": "postgres:16", **settings}, environment={"DB_HOST": "db", "DB_PORT": "55432"})
                self.assertEqual(diagnose(project), [])

    def test_missing_required_dependency_has_precise_source(self):
        project = self.project({"api": {"depends_on": ["typo"]}}, locations={("services", "api", "depends_on", 0): SourceLocation("compose.yaml", 8)})
        finding, = diagnose(project)
        self.assertEqual((finding.code, finding.severity, finding.evidence[0].line), ("WNU008", "error", 8))
        self.assertIn("no service", finding.explanation)

    def test_inactive_profile_dependency_is_distinguished(self):
        api = {"depends_on": {"db": {"condition": "service_started"}}}
        project = self.project({"api": api}, raw_services={"api": api, "db": {"profiles": ["storage"]}})
        finding, = diagnose(project)
        self.assertEqual(finding.title, "Required dependency is inactive")
        self.assertIn("--profile storage", finding.suggestion.description)

    def test_optional_missing_or_inactive_dependency_is_valid(self):
        api = {"depends_on": {"optional": {"required": False}}}
        for raw in [{"api": api}, {"api": api, "optional": {"profiles": ["extras"]}}]:
            with self.subTest(raw=raw):
                self.assertEqual(diagnose(self.project({"api": api}, raw_services=raw)), [])

    def test_real_broken_and_fixed_fixtures(self):
        from whynotup.compose import load_project

        examples = Path(__file__).resolve().parents[1] / "examples"
        broken = load_project(examples / "broken", environ={})
        self.assertEqual(self.codes(broken), ["WNU003", "WNU005", "WNU006"])
        fixed = load_project(examples / "fixed", environ={})
        self.assertEqual(diagnose(fixed), [])


if __name__ == "__main__":
    unittest.main()
