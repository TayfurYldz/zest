from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.data.postgres.tables import SPINE_TABLES, metadata

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "src" / "research_os" / "data"
ALEMBIC_ENV = REPO_ROOT / "alembic" / "env.py"
ALEMBIC_VERSIONS = REPO_ROOT / "alembic" / "versions"
MIGRATION = ALEMBIC_VERSIONS / "a3_001_persistence_spine.py"
A6_MIGRATION = ALEMBIC_VERSIONS / "a6_001_transition_a_provenance.py"
A7_MIGRATION = ALEMBIC_VERSIONS / "a7_001_execution_attempt.py"
A8_MIGRATION = ALEMBIC_VERSIONS / "a8_001_research_reasoning.py"
A9_MIGRATION = ALEMBIC_VERSIONS / "a9_001_learning_cycle.py"
A10_MIGRATION = ALEMBIC_VERSIONS / "a10_001_evidence_admission.py"
A11_MIGRATION = ALEMBIC_VERSIONS / "a11_001_candidate_verification.py"
A12_MIGRATION = ALEMBIC_VERSIONS / "a12_001_finding_acceptance.py"
A13_MIGRATION = ALEMBIC_VERSIONS / "a13_001_target_differential.py"
A14_MIGRATION = ALEMBIC_VERSIONS / "a14_001_invariant_chain.py"
A15_MIGRATION = ALEMBIC_VERSIONS / "a15_001_exploration_temporal.py"
A16_MIGRATION = ALEMBIC_VERSIONS / "a16_001_orchestration_operations.py"
A17_MIGRATION = ALEMBIC_VERSIONS / "a17_001_qa_remediation.py"
A18_MIGRATION = ALEMBIC_VERSIONS / "a18_001_http_auth_class.py"
A19_MIGRATION = ALEMBIC_VERSIONS / "a19_001_http_state_class.py"
A20_MIGRATION = ALEMBIC_VERSIONS / "a20_001_capability_plan_binding.py"
A21_MIGRATION = ALEMBIC_VERSIONS / "a21_001_session_context.py"
A22_MIGRATION = ALEMBIC_VERSIONS / "a22_001_discovery_surface.py"
A23_MIGRATION = ALEMBIC_VERSIONS / "a23_001_program_scope.py"
A24_MIGRATION = ALEMBIC_VERSIONS / "a24_001_sensor_plane.py"
A25_MIGRATION = ALEMBIC_VERSIONS / "a25_001_discovery_fact_kinds.py"
A26_MIGRATION = ALEMBIC_VERSIONS / "a26_001_sensor_obs_src.py"
A27_MIGRATION = ALEMBIC_VERSIONS / "a27_001_attack_surface_snapshot.py"
A28_MIGRATION = ALEMBIC_VERSIONS / "a28_001_token_economy.py"
A29_MIGRATION = ALEMBIC_VERSIONS / "a29_001_hunter_family_registry.py"
A32_MIGRATION = ALEMBIC_VERSIONS / "a32_001_coverage_debt_snapshot.py"
A33_MIGRATION = ALEMBIC_VERSIONS / "a33_001_hypothesis_identity.py"
A34_MIGRATION = ALEMBIC_VERSIONS / "a34_001_program_platforms.py"
A35_MIGRATION = ALEMBIC_VERSIONS / "a35_001_orchestration_lease.py"
A36_MIGRATION = ALEMBIC_VERSIONS / "a36_001_opportunity_candidate.py"
A37_MIGRATION = ALEMBIC_VERSIONS / "a37_001_impact_edge_proof.py"
A38_MIGRATION = ALEMBIC_VERSIONS / "a38_001_promotion_run.py"
A39_MIGRATION = ALEMBIC_VERSIONS / "a39_001_mr5_durability_uq.py"


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class AlembicSmokeTests(unittest.TestCase):
    def test_spine_tables_are_exactly_the_a3_set(self) -> None:
        names = {table.name for table in SPINE_TABLES}
        self.assertEqual(
            names,
            {
                "program",
                "authorization_source",
                "research_run",
                "issued_budget",
                "hypothesis",
                "experiment",
                "execution_attempt",
                "worker_result",
                "observation",
                "audit_event",
                "research_reasoning",
                "research_admission",
                "experiment_plan",
                "hypothesis_assessment",
                "evidence",
                "evidence_observation",
                "evidence_admission",
                "candidate",
                "candidate_evidence",
                "candidate_admission",
                "verification",
                "promotion_run",
                "finding_proposal",
                "human_review",
                "approval",
                "finding",
                "target_inference",
                "differential_observation",
                "invariant_hypothesis",
                "invariant_source_ref",
                "invariant_counterexample_ref",
                "chain_hypothesis",
                "research_opportunity",
                "research_selection",
                "opportunity_selection_candidate",
                "snapshot",
                "snapshot_member",
                "change_event",
                "research_orchestration",
                "research_cycle",
                "budget_consumption",
                "session_context",
                "discovery_run_config",
                "control_event",
                "discovery_fact",
                "discovery_inference",
                "discovery_inference_source",
                "discovery_fact_source",
                "frontier_item",
                "frontier_source",
                "frontier_event",
                "discovery_projection_receipt",
                "attack_surface_snapshot",
                "scope_rule_v2",
                "program_policy",
                "rate_limit_profile",
                "oast_token",
                "bounty_table",
                "sensor_observation",
                "hunter_family",
                "hunt_v3_queue",
                "coverage_debt_snapshot",
                "impact_chain",
                "impact_chain_node",
                "impact_chain_edge",
            },
        )
        self.assertEqual(set(metadata.tables), names)

    def test_deferred_domain_tables_are_absent(self) -> None:
        forbidden = {
            "scope_rule",
            "vector",
            "embedding",
            "attack_surface_node",
            "attack_surface_edge",
        }
        self.assertTrue(forbidden.isdisjoint(metadata.tables))

    def test_data_package_outside_postgres_does_not_import_sqlalchemy(self) -> None:
        violations: list[str] = []
        for path in DATA_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name in _imported_modules(tree):
                root = name.split(".", 1)[0]
                if root in {"sqlalchemy", "psycopg", "alembic"}:
                    violations.append(f"{path.name} imports {name}")
        self.assertEqual(violations, [])

    def test_adapter_does_not_call_create_all(self) -> None:
        for path in [
            *DATA_DIR.rglob("*.py"),
            ALEMBIC_ENV,
        ]:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "create_all",
                source,
                msg=f"{path} must not use metadata.create_all as startup/schema strategy",
            )

    def test_first_migration_is_the_spine_revision(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a3_001_persistence_spine", source)
        self.assertIn("research_os_reject_mutation", source)
        self.assertNotIn("create_all", source)

    def test_a6_migration_is_append_only_revision(self) -> None:
        source = A6_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a6_001_transition_a_provenance", source)
        self.assertIn("a3_001_persistence_spine", source)
        self.assertIn("uq_worker_result_request_id", source)
        self.assertNotIn("create_all", source)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("uq_worker_result_request_id", a3)

    def test_a7_migration_is_append_only_revision(self) -> None:
        source = A7_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a7_001_execution_attempt", source)
        self.assertIn("a6_001_transition_a_provenance", source)
        self.assertIn("uq_execution_attempt_request_id", source)
        self.assertNotIn("create_all", source)
        a6 = A6_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("execution_attempt", a6)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("execution_attempt", a3)

    def test_a8_migration_is_append_only_revision(self) -> None:
        source = A8_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a8_001_research_reasoning", source)
        self.assertIn("a7_001_execution_attempt", source)
        self.assertIn("research_reasoning", source)
        self.assertNotIn("create_all", source)
        a7 = A7_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("research_reasoning", a7)

    def test_a9_migration_is_append_only_revision(self) -> None:
        source = A9_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a9_001_learning_cycle", source)
        self.assertIn("a8_001_research_reasoning", source)
        self.assertIn("research_admission", source)
        self.assertIn("experiment_plan", source)
        self.assertIn("hypothesis_assessment", source)
        self.assertNotIn("create_all", source)
        a8 = A8_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("research_admission", a8)
        self.assertNotIn("hypothesis_assessment", a8)

    def test_a10_migration_is_append_only_revision(self) -> None:
        source = A10_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a10_001_evidence_admission", source)
        self.assertIn("a9_001_learning_cycle", source)
        self.assertIn("evidence_admission", source)
        self.assertNotIn("create_all", source)
        a9 = A9_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("evidence_admission", a9)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE evidence", a3)

    def test_a11_migration_is_append_only_revision(self) -> None:
        source = A11_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a11_001_candidate_verification", source)
        self.assertIn("a10_001_evidence_admission", source)
        self.assertIn("candidate_admission", source)
        self.assertIn("verification", source)
        self.assertNotIn("create_all", source)
        a10 = A10_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE candidate", a10)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE candidate", a3)

    def test_a12_migration_is_append_only_revision(self) -> None:
        source = A12_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a12_001_finding_acceptance", source)
        self.assertIn("a11_001_candidate_verification", source)
        self.assertIn("finding_proposal", source)
        self.assertIn("human_review", source)
        self.assertIn("approval", source)
        self.assertIn("finding", source)
        self.assertNotIn("create_all", source)
        a11 = A11_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE finding", a11)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE finding", a3)

    def test_a13_migration_is_append_only_revision(self) -> None:
        source = A13_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a13_001_target_differential", source)
        self.assertIn("a12_001_finding_acceptance", source)
        self.assertIn("target_inference", source)
        self.assertIn("differential_observation", source)
        self.assertNotIn("create_all", source)
        a12 = A12_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE target_inference", a12)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE target_inference", a3)

    def test_a14_migration_is_append_only_revision(self) -> None:
        source = A14_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a14_001_invariant_chain", source)
        self.assertIn("a13_001_target_differential", source)
        self.assertIn("invariant_hypothesis", source)
        self.assertIn("chain_hypothesis", source)
        self.assertNotIn("create_all", source)
        a13 = A13_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE invariant_hypothesis", a13)
        self.assertNotIn("CREATE TABLE chain_hypothesis", a13)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE invariant_hypothesis", a3)

    def test_a15_migration_is_append_only_revision(self) -> None:
        source = A15_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a15_001_exploration_temporal", source)
        self.assertIn("a14_001_invariant_chain", source)
        self.assertIn("research_opportunity", source)
        self.assertIn("research_selection", source)
        self.assertIn("snapshot", source)
        self.assertIn("change_event", source)
        self.assertNotIn("create_all", source)
        a14 = A14_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE research_opportunity", a14)
        self.assertNotIn("CREATE TABLE snapshot", a14)
        self.assertNotIn("CREATE TABLE change_event", a14)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE research_opportunity", a3)
        self.assertNotIn("CREATE TABLE snapshot", a3)

    def test_a16_migration_is_append_only_revision(self) -> None:
        source = A16_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a16_001_orchestration_operations", source)
        self.assertIn("a15_001_exploration_temporal", source)
        self.assertIn("research_orchestration", source)
        self.assertIn("research_cycle", source)
        self.assertIn("budget_consumption", source)
        self.assertNotIn("create_all", source)
        a15 = A15_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE research_orchestration", a15)
        self.assertNotIn("CREATE TABLE budget_consumption", a15)
        a3 = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE research_orchestration", a3)
        self.assertNotIn("CREATE TABLE budget_consumption", a3)

    def test_a17_migration_does_not_edit_a16(self) -> None:
        source = A17_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a17_001_qa_remediation", source)
        self.assertIn("a16_001_orchestration_operations", source)
        self.assertIn("configuration_fingerprint", source)
        self.assertIn("current_phase", source)
        self.assertNotIn("create_all", source)
        a16 = A16_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("configuration_fingerprint", a16)

    def test_a18_migration_is_append_only_classification_extension(self) -> None:
        source = A18_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a18_001_http_auth_class", source)
        self.assertIn("a17_001_qa_remediation", source)
        self.assertIn("HTTP_AUTHORIZATION_DIFFERENTIAL", source)
        self.assertNotIn("create_all", source)
        a17 = A17_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("HTTP_AUTHORIZATION_DIFFERENTIAL", a17)

    def test_a19_migration_is_append_only_classification_extension(self) -> None:
        source = A19_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a19_001_http_state_class", source)
        self.assertIn("a18_001_http_auth_class", source)
        self.assertIn("HTTP_STATE_TRANSITION_AUTHORIZATION", source)
        self.assertNotIn("create_all", source)
        a18 = A18_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("HTTP_STATE_TRANSITION_AUTHORIZATION", a18)

    def test_a20_migration_adds_plan_capability_binding(self) -> None:
        source = A20_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a20_001_capability_plan_binding", source)
        self.assertIn("a19_001_http_state_class", source)
        self.assertIn("capability_version", source)
        self.assertIn("capability_definition_fingerprint", source)
        self.assertNotIn("compiled_scope_fingerprint", source.split("def upgrade", 1)[1])
        self.assertNotIn("create_all", source)
        a19 = A19_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("capability_definition_fingerprint", a19)

    def test_a21_migration_adds_session_context_metadata_only(self) -> None:
        source = A21_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a21_001_session_context", source)
        self.assertIn("a20_001_capability_plan_binding", source)
        self.assertIn("session_context", source)
        self.assertIn("secret_name", source)
        self.assertNotIn("create_all", source)
        upgrade = source.split("def upgrade", 1)[1]
        self.assertNotIn("cookie_value", upgrade)
        self.assertNotIn("password", upgrade)
        self.assertNotIn("token_value", upgrade)
        a20 = A20_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("session_context", a20)

    def test_a22_migration_is_append_only_discovery_surface(self) -> None:
        source = A22_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a22_001_discovery_surface", source)
        self.assertIn("a21_001_session_context", source)
        self.assertIn("discovery_run_config", source)
        self.assertIn("control_event", source)
        self.assertIn("discovery_fact_source", source)
        self.assertIn("discovery_projection_receipt", source)
        self.assertIn("uq_frontier_event_selected_generation", source)
        self.assertNotIn("attack_surface_node", source)
        self.assertNotIn("create_all", source)
        a21 = A21_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("discovery_run_config", a21)
        self.assertNotIn("control_event", a21)

    def test_a23_migration_is_program_scope(self) -> None:
        source = A23_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a23_001_program_scope", source)
        self.assertIn("a22_001_discovery_surface", source)
        self.assertIn("scope_rule_v2", source)
        self.assertIn("program_policy", source)
        self.assertNotIn("create_all", source)
        a22 = A22_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("scope_rule_v2", a22)
        self.assertNotIn("program_policy", a22)

    def test_a24_migration_is_append_only_sensor_plane(self) -> None:
        source = A24_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a24_001_sensor_plane", source)
        self.assertIn("a23_001_program_scope", source)
        self.assertIn("sensor_observation", source)
        self.assertIn("UNTRUSTED_EXTERNAL", source)
        self.assertNotIn("create_all", source)
        a23 = A23_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("sensor_observation", a23)

    def test_a25_migration_expands_discovery_fact_kinds(self) -> None:
        source = A25_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a25_001_discovery_fact_kinds", source)
        self.assertIn("a24_001_sensor_plane", source)
        self.assertIn("ck_discovery_fact_kind", source)
        self.assertIn("DOMAIN", source)
        self.assertIn("HOSTNAME", source)
        self.assertIn("CERT", source)
        self.assertNotIn("create_all", source)
        a24 = A24_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("DOMAIN", a24)

    def test_a26_migration_adds_sensor_observation_source(self) -> None:
        source = A26_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a26_001_sensor_obs_src", source)
        self.assertIn("a25_001_discovery_fact_kinds", source)
        self.assertIn("sensor_observation_id", source)
        self.assertIn("discovery_fact_source", source)
        self.assertNotIn("create_all", source)
        a25 = A25_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("sensor_observation_id", a25)

    def test_a27_migration_adds_attack_surface_snapshot(self) -> None:
        source = A27_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a27_001_attack_surface_snapshot", source)
        self.assertIn("a26_001_sensor_obs_src", source)
        self.assertIn("attack_surface_snapshot", source)
        self.assertIn("graph_hash", source)
        self.assertIn("node_count", source)
        self.assertIn("edge_count", source)
        self.assertNotIn("create_all", source)
        a26 = A26_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("attack_surface_snapshot", a26)

    def test_a28_migration_adds_token_economy(self) -> None:
        source = A28_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a28_001_token_economy", source)
        self.assertIn("a27_001_attack_surface_snapshot", source)
        self.assertIn("daily_llm_budget_microdollars", source)
        self.assertIn("resource_metadata", source)
        self.assertIn("MODEL_TOKENS_IN", source)
        self.assertIn("MODEL_TOKENS_OUT", source)
        self.assertNotIn("create_all", source)
        a27 = A27_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("daily_llm_budget_microdollars", a27)
        self.assertNotIn("resource_metadata", a27)

    def test_a32_migration_adds_coverage_debt_snapshot(self) -> None:
        source = A32_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a32_001_coverage_debt_snapshot", source)
        self.assertIn("a31_001_impact_graph", source)
        self.assertIn("coverage_debt_snapshot", source)
        self.assertIn("matrix_hash", source)
        self.assertIn("cell_counts", source)
        self.assertIn("total_debt", source)
        self.assertNotIn("create_all", source)
        a31 = ALEMBIC_VERSIONS / "a31_001_impact_graph.py"
        self.assertFalse(a31.exists() and "coverage_debt_snapshot" in a31.read_text(encoding="utf-8"))

    def test_a33_migration_adds_hypothesis_identity(self) -> None:
        source = A33_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a33_001_hypothesis_identity", source)
        self.assertIn("a32_001_coverage_debt_snapshot", source)
        self.assertIn("hypothesis", source)
        self.assertIn("identity_id", source)
        self.assertIn("hunt_v3_queue", source)
        self.assertNotIn("create_all", source)
        a32 = A32_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("identity_id", a32)

    def test_a34_migration_extends_program_platforms(self) -> None:
        source = A34_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a34_001_program_platforms", source)
        self.assertIn("a33_001_hypothesis_identity", source)
        self.assertIn("ck_program_platform", source)
        self.assertIn("yeswehack", source)
        self.assertIn("intigriti", source)
        self.assertIn("other", source)
        self.assertNotIn("create_all", source)

    def test_a35_migration_adds_orchestration_lease(self) -> None:
        source = A35_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a35_001_orchestration_lease", source)
        self.assertIn("a34_001_program_platforms", source)
        self.assertIn("owner_runtime_instance_id", source)
        self.assertIn("lease_epoch", source)
        self.assertIn("lease_expires_at", source)
        self.assertNotIn("create_all", source)
        a34 = A34_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("lease_epoch", a34)

    def test_a36_migration_adds_opportunity_selection_candidate(self) -> None:
        source = A36_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a36_001_opportunity_candidate", source)
        self.assertIn("a35_001_orchestration_lease", source)
        self.assertIn("opportunity_selection_candidate", source)
        self.assertIn("structural_identity", source)
        self.assertIn("HUNTER_COVERAGE", source)
        self.assertNotIn("create_all", source)
        a35 = A35_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("opportunity_selection_candidate", a35)

    def test_a37_migration_adds_impact_edge_proof_refs(self) -> None:
        source = A37_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a37_001_impact_edge_proof", source)
        self.assertIn("a36_001_opportunity_candidate", source)
        self.assertIn("proof_refs", source)
        self.assertIn("impact_chain_edge", source)
        self.assertNotIn("create_all", source)
        a36 = A36_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("a37_001_impact_edge_proof", a36)

    def test_a38_migration_adds_promotion_run(self) -> None:
        source = A38_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a38_001_promotion_run", source)
        self.assertIn("a37_001_impact_edge_proof", source)
        self.assertIn("promotion_run", source)
        self.assertIn("uq_promotion_run_assessment", source)
        self.assertNotIn("create_all", source)
        a37 = A37_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("a38_001_promotion_run", a37)

    def test_a39_migration_adds_durability_uniqueness(self) -> None:
        source = A39_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("a39_001_mr5_durability_uq", source)
        self.assertIn("a38_001_promotion_run", source)
        self.assertIn("uq_candidate_evidence_evidence_id", source)
        self.assertIn("uq_verification_candidate", source)
        self.assertIn("uq_finding_proposal_candidate", source)
        self.assertNotIn("DELETE FROM", source)
        self.assertNotIn("create_all", source)
        a38 = A38_MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("a39_001_mr5_durability_uq", a38)


if __name__ == "__main__":
    unittest.main()
