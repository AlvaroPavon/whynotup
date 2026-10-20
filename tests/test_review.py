from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from whynotup.models import ComposeProject, SourceLocation
from whynotup.review import review_project


class ReviewTests(unittest.TestCase):
    def project(self, services=None, *, raw=None, environment=None, locations=None):
        return ComposeProject(Path("/synthetic"), Path("/synthetic/compose.yaml"), services or {"api": {}},
                              raw if raw is not None else services or {"api": {}}, {}, environment or {}, locations or {})

    def findings(self, project, code, **kwargs):
        return [item for item in review_project(project, **kwargs) if item.code == code]

    def safe_service(self, **changes):
        return {"image": "example/api:1.2", "user": "1000", "restart": "unless-stopped", "cpus": "0.5", "mem_limit": "256m", **changes}

    def test_complete_service_has_no_advice_and_review_is_read_only(self):
        project = self.project({"api": self.safe_service()})
        original = copy.deepcopy(project)
        self.assertEqual(review_project(project), [])
        self.assertEqual(project, original)

    def test_image_tag_parsing_respects_registry_ports_and_digests(self):
        for image, count in [("postgres", 1), ("registry.example:5000/api", 1), ("registry.example:5000/api:latest", 1), ("registry.example:5000/api:1.2", 0), ("api:latest@sha256:synthetic", 0)]:
            with self.subTest(image=image):
                self.assertEqual(len(self.findings(self.project({"api": {"image": image}}), "WNU101")), count)
        self.assertEqual(self.findings(self.project({"api": {"build": "."}}), "WNU101"), [])

    def test_privileged_mode_is_flagged_without_unsafe_automatic_patch(self):
        project = self.project({"api": {"privileged": True}}, locations={("services", "api", "privileged"): SourceLocation("compose.yaml", 4)})
        finding, = self.findings(project, "WNU102")
        self.assertEqual((finding.evidence[0].line, finding.suggestion.line), (4, 4))
        self.assertIsNone(finding.suggestion.after)
        self.assertEqual(self.findings(self.project({"api": {"privileged": False}}), "WNU102"), [])

    def test_only_explicit_root_user_is_flagged(self):
        for user, flagged in [("root", True), ("root:1000", True), (0, True), ("0:root", True), ("1000:0", False), ("app", False), (None, False)]:
            with self.subTest(user=user):
                service = {"user": user} if user is not None else {}
                self.assertEqual(bool(self.findings(self.project({"api": service}), "WNU103")), flagged)

    def test_general_port_bindings_but_not_explicitly_scoped_addresses(self):
        broad = ["8080:80", "0.0.0.0:8081:81", "[::]:8082:82", 83, {"target": 84, "published": 8084}, {"target": 85, "published": 8085, "host_ip": "::"}]
        scoped = ["127.0.0.1:8080:80", "[::1]:8081:81", "192.168.1.2:8082:82", {"target": 83, "published": 8083, "host_ip": "127.0.0.1"}]
        finding, = self.findings(self.project({"api": {"ports": broad + scoped}}), "WNU104")
        self.assertEqual(len(finding.evidence), len(broad))
        self.assertIn("reachability were not inspected", finding.explanation)
        self.assertEqual(self.findings(self.project({"api": {"ports": scoped}}), "WNU104"), [])

    def test_host_network_uses_own_rule_without_false_port_publication_warning(self):
        project = self.project({"api": {"network_mode": "host", "ports": ["8080:80"]}})
        self.assertEqual(self.findings(project, "WNU104"), [])
        self.assertEqual(len(self.findings(project, "WNU110")), 1)
        shared = self.project({"api": {"network_mode": "service:gateway", "ports": ["8080:80"]}, "gateway": {"network_mode": "host"}})
        self.assertEqual(self.findings(shared, "WNU104"), [])

    def test_docker_socket_readonly_does_not_remove_api_risk(self):
        project = self.project({"api": {"volumes": ["/var/run/docker.sock:/daemon.sock:ro", {"type": "bind", "source": "/run/user/1000/docker.sock", "target": "/socket", "read_only": True}]}})
        finding, = self.findings(project, "WNU105")
        self.assertEqual(len(finding.evidence), 2)
        self.assertIn("does not make the socket API read-only", finding.explanation)
        self.assertEqual(finding.confidence, "probable")

    def test_named_and_anonymous_volumes_are_not_assumed_to_be_host_docker_sockets(self):
        project = self.project({"api": {"volumes": ["docker.sock:/var/run/docker.sock", "/var/run/docker.sock", {"type": "volume", "source": "socket-data", "target": "/var/run/docker.sock"}]}})
        self.assertEqual(self.findings(project, "WNU105"), [])

    def test_inline_secret_values_never_appear_in_finding_or_suggestion(self):
        service = {"environment": {"DB_PASSWORD": "synthetic-password", "DATABASE_URL": "postgres://user:synthetic-url-password@db/app", "API_TOKEN": "synthetic-token"}}
        project = self.project({"api": service})
        finding, = self.findings(project, "WNU106")
        self.assertEqual(len(finding.evidence), 3)
        for secret in ["synthetic-password", "synthetic-url-password", "synthetic-token"]:
            self.assertNotIn(secret, repr(finding))
        self.assertIsNone(finding.suggestion.before)
        self.assertIsNone(finding.suggestion.after)

    def test_secret_references_files_pass_through_and_policy_flags_are_not_inline(self):
        raw = {"api": {"environment": {"DB_PASSWORD": "${DB_PASSWORD}", "API_TOKEN": "$API_TOKEN", "CLIENT_SECRET": "${CLIENT_SECRET:?Set it}", "PASSWORD_FILE": "/run/secrets/password", "TOKEN_URL": "https://auth.example", "PASSWORD_REQUIRED": "true", "EMPTY_PASSWORD": "", "PASSTHROUGH_SECRET": None}}}
        project = self.project({"api": {"env_file": "private.env"}}, raw=raw, environment={"api": {"DB_PASSWORD": "resolved-secret"}})
        self.assertEqual(self.findings(project, "WNU106"), [])

    def test_secret_literal_fallback_is_advice_but_empty_fallback_is_not(self):
        project = self.project({"api": {"environment": {"DB_PASSWORD": "${DB_PASSWORD:-literal-fallback}", "API_TOKEN": "${API_TOKEN:-}"}}})
        finding, = self.findings(project, "WNU106")
        self.assertEqual(len(finding.evidence), 1)
        self.assertNotIn("literal-fallback", repr(finding))

    def test_list_environment_uses_last_declaration_and_precise_source(self):
        project = self.project({"api": {"environment": ["DB_PASSWORD=overridden-literal", "DB_PASSWORD=${DB_PASSWORD}", "API_TOKEN=synthetic-token"]}},
                               locations={("services", "api", "environment", 2): SourceLocation("compose.yaml", 9)})
        finding, = self.findings(project, "WNU106")
        self.assertEqual(len(finding.evidence), 1)
        self.assertEqual(finding.evidence[0].line, 9)

    def test_started_dependency_is_not_readiness_even_with_existing_healthcheck(self):
        project = self.project({"api": {"depends_on": {"db": {"condition": "service_started"}}}, "db": {"healthcheck": {"test": ["CMD", "check-ready"]}}})
        finding, = self.findings(project, "WNU107")
        self.assertIn("even when a healthcheck is defined", finding.explanation)
        self.assertIn("verify its existing healthcheck", finding.suggestion.description)
        self.assertIsNone(finding.suggestion.after)

    def test_short_dependencies_suggest_no_fabricated_healthcheck_command(self):
        project = self.project({"api": {"depends_on": ["db"]}, "db": {}})
        finding, = self.findings(project, "WNU107")
        self.assertIn("workload-specific healthcheck", finding.suggestion.description)
        self.assertNotIn("pg_isready", repr(finding))

    def test_readiness_skips_healthy_completed_optional_missing_and_job_targets(self):
        project = self.project({"api": {"depends_on": {"healthy": {"condition": "service_healthy"}, "job": {"condition": "service_completed_successfully"}, "optional": {"required": False}, "missing": {}}}, "healthy": {}, "job": {}, "optional": {}})
        self.assertEqual(self.findings(project, "WNU107"), [])

    def test_limits_accept_service_and_deploy_settings_but_not_reservations(self):
        valid = [{"cpus": "0.5", "mem_limit": "256m"}, {"deploy": {"resources": {"limits": {"cpus": "1", "memory": "1GiB"}}}}, {"cpu_quota": 50000, "mem_limit": 268435456}]
        for settings in valid:
            with self.subTest(settings=settings):
                self.assertEqual(self.findings(self.project({"api": settings}), "WNU108"), [])
        project = self.project({"api": {"cpus": "0", "mem_limit": "0m", "deploy": {"resources": {"reservations": {"cpus": "1", "memory": "256m"}}}}})
        finding, = self.findings(project, "WNU108")
        self.assertIn("CPU and memory", finding.explanation)

    def test_partial_resource_limit_only_mentions_missing_dimension(self):
        finding, = self.findings(self.project({"api": {"cpus": "1"}}), "WNU108")
        self.assertIn("No positive memory limit", finding.explanation)

    def test_unrepresentably_large_numeric_limits_do_not_crash_or_count_as_valid(self):
        oversized = 10 ** 400
        for setting in ("cpus", "cpu_quota", "mem_limit"):
            with self.subTest(setting=setting):
                finding, = self.findings(self.project({"api": {setting: oversized}}), "WNU108")
                self.assertIn("CPU and memory", finding.explanation)

    def test_restart_policy_respects_deploy_and_explicit_no_and_completed_jobs(self):
        for settings in [{"restart": "unless-stopped"}, {"restart": "no"}, {"deploy": {"restart_policy": {"condition": "on-failure"}}}, {"deploy": {"mode": "replicated-job"}}]:
            with self.subTest(settings=settings):
                self.assertEqual(self.findings(self.project({"api": settings}), "WNU109"), [])
        project = self.project({"api": {"restart": "unless-stopped", "depends_on": {"migration": {"condition": "service_completed_successfully"}}}, "migration": {}})
        self.assertEqual(self.findings(project, "WNU109"), [])
        self.assertEqual(len(self.findings(self.project(), "WNU109")), 1)

    def test_known_database_without_persistent_mount_is_probable_advice(self):
        project = self.project({"db": self.safe_service(image="postgres:16")})
        finding, = self.findings(project, "WNU111")
        self.assertEqual(finding.confidence, "probable")
        self.assertIn("intentionally disposable", finding.explanation)

    def test_database_persistence_accepts_named_bind_parent_and_pgdata_mounts(self):
        cases = [({"volumes": ["data:/var/lib/postgresql/data"]}, {}), ({"volumes": ["./data:/var/lib/postgresql/data"]}, {}), ({"volumes": ["data:/var/lib/postgresql"]}, {}), ({"volumes": [{"type": "volume", "source": "data", "target": "/custom"}]}, {"db": {"PGDATA": "/custom/pgdata"}})]
        for settings, environment in cases:
            with self.subTest(settings=settings):
                project = self.project({"db": self.safe_service(image="postgres:16", **settings)}, environment=environment)
                self.assertEqual(self.findings(project, "WNU111"), [])

    def test_config_only_readonly_or_tmpfs_mount_is_not_durable_database_data(self):
        for volumes in [["./config:/etc/postgresql"], ["data:/var/lib/postgresql/data:ro"], [{"type": "tmpfs", "target": "/var/lib/postgresql/data"}]]:
            with self.subTest(volumes=volumes):
                project = self.project({"db": self.safe_service(image="postgres:16", volumes=volumes)})
                self.assertEqual(len(self.findings(project, "WNU111")), 1)

    def test_database_clients_jobs_and_unknown_images_do_not_get_storage_assumption(self):
        for settings in [{"image": "postgres:16", "command": ["pg_dump", "database"]}, {"image": "postgres:16", "restart": "no"}, {"image": "custom/database:1"}, {"image": "mysql:8", "volumes_from": ["storage"]}]:
            with self.subTest(settings=settings):
                self.assertEqual(self.findings(self.project({"db": settings}), "WNU111"), [])

    def test_postgres_18_parent_data_path_is_recognized(self):
        project = self.project({"db": self.safe_service(image="postgres:18", volumes=["data:/var/lib/postgresql"])})
        self.assertEqual(self.findings(project, "WNU111"), [])

    def test_development_omits_production_only_capacity_recovery_and_storage_advice(self):
        project = self.project({"db": {"image": "postgres:latest", "privileged": True}})
        codes = {finding.code for finding in review_project(project, target="development")}
        self.assertEqual(codes, {"WNU101", "WNU102"})
        self.assertTrue({"WNU108", "WNU109", "WNU111"}.issubset({finding.code for finding in review_project(project)}))

    def test_invalid_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "production or development"):
            review_project(self.project(), target="unknown")

    def test_unrecognized_nested_shapes_are_not_misclassified_or_executed(self):
        project = self.project({"api": {"depends_on": [{"unexpected": "value"}], "deploy": {"mode": {"unexpected": "value"}}, "volumes": [{"type": {"unexpected": "value"}, "target": "/data"}]}})
        self.assertEqual(self.findings(project, "WNU107"), [])
        self.assertEqual(self.findings(project, "WNU105"), [])

    def test_loader_integration_uses_raw_secret_provenance(self):
        from whynotup.compose import load_project

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yaml").write_text('services:\n  api:\n    image: example/api:1\n    environment:\n      DB_PASSWORD: ${DB_PASSWORD}\n      API_TOKEN: inline-synthetic-token\n', encoding="utf-8")
            (root / ".env").write_text("DB_PASSWORD=external-synthetic-password\n", encoding="utf-8")
            project = load_project(root, environ={})
            finding, = self.findings(project, "WNU106")
            self.assertEqual(finding.evidence[0].line, 6)
            self.assertEqual(len(finding.evidence), 1)
            self.assertNotIn("inline-synthetic-token", repr(finding))
            self.assertNotIn("external-synthetic-password", repr(finding))


if __name__ == "__main__":
    unittest.main()
