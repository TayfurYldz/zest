from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "zest"
CORE_DIR = SRC_ROOT / "core"
RESEARCH_DIR = SRC_ROOT / "research"
APPLICATION_DIR = SRC_ROOT / "application"
BENCHMARK_DIR = SRC_ROOT / "benchmark"
SECURITY_BENCHMARK_DIR = SRC_ROOT / "security_benchmark"
PLATFORM_DIR = SRC_ROOT / "platform"
WORKERS_DIR = REPO_ROOT / "workers"

PERSISTENCE_LIBS = ("sqlalchemy", "psycopg", "alembic")
SCHEMA_LIBS = ("jsonschema", "referencing")
EXECUTION_ROOTS = (
    "workers",
    "integrations",
    "strix",
    "subprocess",
    "socket",
    "requests",
)
FORBIDDEN_EXACT = {"urllib.request"}


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            if node.module == "urllib":
                for alias in node.names:
                    names.add(f"urllib.{alias.name}")
    return names


def _violations(
    directory: Path,
    *,
    forbidden_roots: tuple[str, ...],
    forbidden_prefixes: tuple[str, ...] = (),
) -> list[str]:
    if not directory.exists():
        return []
    found: list[str] = []
    for path in directory.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _imported_modules(tree):
            if name in FORBIDDEN_EXACT:
                found.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
                continue
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in forbidden_prefixes
            ):
                found.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
                continue
            root = name.split(".", 1)[0]
            if root in forbidden_roots:
                found.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
    return found


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_core_does_not_import_forbidden_namespaces(self) -> None:
        self.assertEqual(
            _violations(
                CORE_DIR,
                forbidden_roots=EXECUTION_ROOTS + PERSISTENCE_LIBS + SCHEMA_LIBS + ("playwright",),
                forbidden_prefixes=(
                    "zest.data",
                    "zest.workers",
                    "zest.platform.local_process_worker",
                    "zest.application",
                    "zest.research",
                    "zest.benchmark",
                    "zest.security_benchmark",
                    "zest.integrations",
                ),
            ),
            [],
        )

    def test_research_does_not_import_sqlalchemy_or_postgres_adapter(self) -> None:
        self.assertEqual(
            _violations(
                RESEARCH_DIR,
                forbidden_roots=EXECUTION_ROOTS
                + PERSISTENCE_LIBS
                + SCHEMA_LIBS
                + (
                    "openai",
                    "anthropic",
                    "google",
                    "langchain",
                    "llama_index",
                    "litellm",
                    "neo4j",
                    "networkx",
                    "chromadb",
                    "faiss",
                    "pinecone",
                    "playwright",
                ),
                forbidden_prefixes=(
                    "zest.data",
                    "zest.workers",
                    "zest.platform",
                    "zest.application",
                    "zest.benchmark",
                    "zest.security_benchmark",
                    "zest.integrations",
                    "google.generativeai",
                    "google.genai",
                ),
            ),
            [],
        )

    def test_application_does_not_import_concrete_adapters(self) -> None:
        self.assertEqual(
            _violations(
                APPLICATION_DIR,
                forbidden_roots=EXECUTION_ROOTS
                + PERSISTENCE_LIBS
                + ("openai", "anthropic", "google", "langchain", "playwright"),
                forbidden_prefixes=(
                    "zest.data.postgres",
                    "zest.platform.local_process_worker",
                    "zest.platform.persistent_browser_worker",
                    "zest.workers",
                    "zest.benchmark",
                    "zest.security_benchmark",
                    "integrations",
                    "zest.integrations",
                    "google.generativeai",
                    "google.genai",
                ),
            ),
            [],
        )

    def test_application_discovery_does_not_import_playwright(self) -> None:
        self.assertEqual(
            _violations(
                APPLICATION_DIR / "discovery",
                forbidden_roots=("playwright", "neo4j", "networkx"),
            ),
            [],
        )

    def test_data_does_not_import_research_or_discovery_decisions(self) -> None:
        found: list[str] = []
        data_dir = SRC_ROOT / "data"
        needles = (
            "select_surface_discovery_opportunities",
            "admit_route_template_inferences",
            "rebuild_attack_surface_graph",
            "select_eligible_frontier",
        )
        for path in data_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            for name in _imported_modules(tree):
                if name == "zest.research" or name.startswith("zest.research."):
                    found.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
            for needle in needles:
                if needle in text:
                    found.append(f"{path.relative_to(REPO_ROOT)} references {needle}")
        self.assertEqual(found, [])

    def test_benchmark_does_not_import_sor_providers_or_workers(self) -> None:
        self.assertEqual(
            _violations(
                BENCHMARK_DIR,
                forbidden_roots=EXECUTION_ROOTS
                + PERSISTENCE_LIBS
                + SCHEMA_LIBS
                + ("openai", "anthropic", "google", "langchain", "llama_index", "litellm"),
                forbidden_prefixes=(
                    "zest.data",
                    "zest.application",
                    "zest.workers",
                    "zest.platform",
                    "zest.integrations",
                    "zest.security_benchmark",
                    "google.generativeai",
                    "google.genai",
                ),
            ),
            [],
        )

    def test_security_benchmark_does_not_import_pipeline_or_research_benchmark(self) -> None:
        self.assertEqual(
            _violations(
                SECURITY_BENCHMARK_DIR,
                forbidden_roots=EXECUTION_ROOTS
                + PERSISTENCE_LIBS
                + SCHEMA_LIBS
                + ("openai", "anthropic", "google", "langchain", "llama_index", "litellm"),
                forbidden_prefixes=(
                    "zest.data",
                    "zest.application",
                    "zest.workers",
                    "zest.platform",
                    "zest.integrations",
                    "zest.benchmark",
                    "zest.research",
                    "zest.core",
                    "google.generativeai",
                    "google.genai",
                ),
            ),
            [],
        )

    def test_platform_does_not_import_application_or_research(self) -> None:
        self.assertEqual(
            _violations(
                PLATFORM_DIR,
                forbidden_roots=(),
                forbidden_prefixes=("zest.application", "zest.research"),
            ),
            [],
        )

    def test_workers_do_not_import_application(self) -> None:
        self.assertEqual(
            _violations(
                WORKERS_DIR,
                forbidden_roots=PERSISTENCE_LIBS + SCHEMA_LIBS + ("zest",),
                forbidden_prefixes=("zest.data", "zest.application"),
            ),
            [],
        )
        self.assertEqual(
            _violations(
                SRC_ROOT / "worker_runtime",
                forbidden_roots=PERSISTENCE_LIBS + SCHEMA_LIBS,
                forbidden_prefixes=(
                    "zest.data",
                    "zest.application",
                    "zest.core",
                    "zest.research",
                    "zest.integrations",
                ),
            ),
            [],
        )

    def test_local_process_adapter_does_not_use_shell_or_data(self) -> None:
        path = SRC_ROOT / "platform" / "local_process_worker.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("shell=False", text)
        self.assertNotIn("shell=True", text)
        tree = ast.parse(text, filename=str(path))
        found = []
        for name in _imported_modules(tree):
            root = name.split(".", 1)[0]
            if root in PERSISTENCE_LIBS or name.startswith("zest.data") or name.startswith(
                "zest.core"
            ) or name.startswith("zest.research"):
                found.append(name)
        self.assertEqual(found, [])

    def test_platform_ports_do_not_import_subprocess(self) -> None:
        port_files = [
            SRC_ROOT / "platform" / "worker.py",
            SRC_ROOT / "platform" / "contract_validation.py",
            SRC_ROOT / "platform" / "strix.py",
            SRC_ROOT / "platform" / "__init__.py",
        ]
        found = []
        for path in port_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name in _imported_modules(tree):
                if name.split(".", 1)[0] == "subprocess":
                    found.append(f"{path.name} imports {name}")
        self.assertEqual(found, [])

    def test_argv_process_adapter_does_not_use_shell_or_domain(self) -> None:
        path = SRC_ROOT / "platform" / "argv_process.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("shell=False", text)
        self.assertNotRegex(text, r"shell\s*=\s*True")
        self.assertNotIn("argv_process", (SRC_ROOT / "platform" / "__init__.py").read_text(encoding="utf-8"))
        tree = ast.parse(text, filename=str(path))
        found = []
        for name in _imported_modules(tree):
            if name.startswith("zest.data") or name.startswith("zest.core") or name.startswith(
                "zest.research"
            ):
                found.append(name)
        self.assertEqual(found, [])

    def test_strix_adapter_does_not_import_data_or_core(self) -> None:
        path = SRC_ROOT / "integrations" / "strix" / "adapter.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = []
        for name in _imported_modules(tree):
            if name.startswith("zest.data") or name.startswith("zest.core"):
                found.append(name)
        self.assertEqual(found, [])

    def test_agent_runtime_adapters_do_not_import_core(self) -> None:
        paths = [
            SRC_ROOT / "integrations" / "models" / "cli_session.py",
            SRC_ROOT / "integrations" / "models" / "external_agent.py",
        ]
        found = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name in _imported_modules(tree):
                if name.startswith("zest.core") or name.startswith("zest.data"):
                    found.append(f"{path.name} imports {name}")
        self.assertEqual(found, [])

    def test_http_authentication_worker_copies_stay_in_sync(self) -> None:
        runtime = SRC_ROOT / "worker_runtime" / "python" / "http_authentication.py"
        packaged = WORKERS_DIR / "python" / "zest_worker" / "http_authentication.py"
        self.assertEqual(
            runtime.read_text(encoding="utf-8"),
            packaged.read_text(encoding="utf-8"),
        )

    def test_http_raw_exchange_worker_copies_stay_in_sync(self) -> None:
        runtime = SRC_ROOT / "worker_runtime" / "python" / "http_raw_exchange.py"
        packaged = WORKERS_DIR / "python" / "zest_worker" / "http_raw_exchange.py"
        self.assertEqual(
            runtime.read_text(encoding="utf-8"),
            packaged.read_text(encoding="utf-8"),
        )

    def test_http_transaction_worker_copies_stay_in_sync(self) -> None:
        runtime = SRC_ROOT / "worker_runtime" / "python" / "http_transaction.py"
        packaged = WORKERS_DIR / "python" / "zest_worker" / "http_transaction.py"
        self.assertEqual(
            runtime.read_text(encoding="utf-8"),
            packaged.read_text(encoding="utf-8"),
        )

    def test_http_state_transition_worker_copies_stay_in_sync(self) -> None:
        runtime = SRC_ROOT / "worker_runtime" / "python" / "http_state_transition.py"
        packaged = WORKERS_DIR / "python" / "zest_worker" / "http_state_transition.py"
        self.assertEqual(
            runtime.read_text(encoding="utf-8"),
            packaged.read_text(encoding="utf-8"),
        )

    def test_worker_capability_modules_stay_in_sync(self) -> None:
        names = (
            "capabilities.py",
            "implementation.py",
            "packaged_registry.py",
            "fingerprint.py",
        )
        for name in names:
            runtime = SRC_ROOT / "worker_runtime" / "python" / name
            packaged = WORKERS_DIR / "python" / "zest_worker" / name
            self.assertEqual(
                runtime.read_text(encoding="utf-8"),
                packaged.read_text(encoding="utf-8"),
                msg=name,
            )

    def test_canonical_worker_capability_json_matches_packaged_copies(self) -> None:
        canonical = SRC_ROOT / "resources" / "contracts" / "v1" / "capabilities"
        runtime = SRC_ROOT / "worker_runtime" / "python" / "resources" / "capabilities"
        packaged = WORKERS_DIR / "python" / "zest_worker" / "resources" / "capabilities"
        worker_files = {path.name for path in runtime.glob("*.json")}
        self.assertEqual(worker_files, {path.name for path in packaged.glob("*.json")})
        self.assertEqual(
            worker_files,
            {
                "browser.page.json",
                "diagnostic.echo.json",
                "http.authentication.json",
                "http.authorization.differential.json",
                "http.raw_exchange.json",
                "http.state_transition.json",
                "http.transaction.json",
            },
        )
        for name in worker_files:
            self.assertEqual(
                (canonical / name).read_text(encoding="utf-8"),
                (runtime / name).read_text(encoding="utf-8"),
                msg=name,
            )
            self.assertEqual(
                (canonical / name).read_text(encoding="utf-8"),
                (packaged / name).read_text(encoding="utf-8"),
                msg=name,
            )
        canonical_files = {path.name for path in canonical.glob("*.json")}
        self.assertEqual(canonical_files, worker_files)
        self.assertNotIn("strix.diagnostic.ping.json", canonical_files)
        self.assertNotIn("codex.diagnostic.structured_output.json", canonical_files)

    def test_worker_request_schema_copies_stay_in_sync(self) -> None:
        canonical = REPO_ROOT / "contracts" / "v1" / "worker" / "worker-request.schema.json"
        packaged = SRC_ROOT / "resources" / "contracts" / "v1" / "worker" / "worker-request.schema.json"
        self.assertEqual(canonical.read_text(encoding="utf-8"), packaged.read_text(encoding="utf-8"))

    def test_browser_worker_modules_stay_in_sync(self) -> None:
        names = (
            "browser_page.py",
            "browser_containment.py",
            "browser_engine.py",
            "browser_envelope.py",
            "browser_route_policy.py",
            "playwright_chromium_engine.py",
            "persistent_runtime.py",
        )
        for name in names:
            runtime = SRC_ROOT / "worker_runtime" / "python" / name
            packaged = WORKERS_DIR / "python" / "zest_worker" / name
            self.assertEqual(
                runtime.read_text(encoding="utf-8"),
                packaged.read_text(encoding="utf-8"),
                msg=name,
            )

    def test_playwright_is_confined_to_worker_engine(self) -> None:
        allowed = {
            SRC_ROOT / "worker_runtime" / "python" / "playwright_chromium_engine.py",
            WORKERS_DIR / "python" / "zest_worker" / "playwright_chromium_engine.py",
        }
        found = []
        for directory in (
            CORE_DIR,
            RESEARCH_DIR,
            APPLICATION_DIR,
            SRC_ROOT / "data",
            SRC_ROOT / "tools",
            PLATFORM_DIR,
            SRC_ROOT / "worker_runtime",
            WORKERS_DIR,
        ):
            if not directory.exists():
                continue
            for path in directory.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "playwright" not in text.lower() and "from playwright" not in text:
                    continue
                tree = ast.parse(text, filename=str(path))
                for name in _imported_modules(tree):
                    root = name.split(".", 1)[0]
                    if root == "playwright" or name.startswith("playwright."):
                        if path.resolve() not in {item.resolve() for item in allowed}:
                            found.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
        self.assertEqual(found, [])

    def test_existing_capability_fingerprints_are_unchanged(self) -> None:
        from zest.tools.fingerprint import fingerprint_capability_document
        from zest.tools.registry import load_capability_registry

        load_capability_registry.cache_clear()
        registry = load_capability_registry()
        expected = {
            "diagnostic.echo": "d86e9a66407367f2853f801a6f537d63dc264cc892c45036703081d806e5c98d",
            "http.authentication": "5284151e468907b7fc30b2db6f614e402776d64112fa42f52381b132a600a26b",
            "http.authorization.differential": "3cdb4f4f2a0d99b1ed568c38481dd2fa414b7afb00a6906f3c3ea99945a85968",
            "http.state_transition": "98156c04bf02b910aa501deabb0564c20a57fdb69fe3bc65a764afc648690d87",
            "http.transaction": "dbb8b079a31842c9b935cc08e02676072ac42954e4e3b34819ccea905382a90b",
        }
        canonical = SRC_ROOT / "resources" / "contracts" / "v1" / "capabilities"
        for capability_id, digest in expected.items():
            definition = registry.get(capability_id)
            assert definition is not None
            document = json.loads(
                (canonical / f"{capability_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(definition.definition_fingerprint, digest)
            self.assertEqual(fingerprint_capability_document(document), digest)
        self.assertEqual(
            _violations(
                SRC_ROOT / "tools",
                forbidden_roots=EXECUTION_ROOTS + PERSISTENCE_LIBS,
                forbidden_prefixes=(
                    "zest.core",
                    "zest.research",
                    "zest.application",
                    "zest.data",
                    "zest.workers",
                    "zest.worker_runtime",
                ),
            ),
            [],
        )

    def test_core_does_not_import_worker_runtime(self) -> None:
        self.assertEqual(
            _violations(
                CORE_DIR,
                forbidden_roots=(),
                forbidden_prefixes=("zest.worker_runtime", "zest.workers"),
            ),
            [],
        )

    def test_production_research_does_not_branch_on_benchmark_ids(self) -> None:
        forbidden_ids = (
            "R01_BOLA_TRUE_WORKFLOW_DECOY",
            "R02_WORKFLOW_TRUE_BOLA_DECOY",
            "R03_BOTH_TRUE",
            "R04_BOTH_BENIGN",
            "R11A_COUNTERFACTUAL_BOLA_PRIVATE",
            "R12A_COUNTERFACTUAL_WORKFLOW_APPROVED",
        )
        found = []
        for directory in (RESEARCH_DIR, APPLICATION_DIR, CORE_DIR):
            for path in directory.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for token in forbidden_ids:
                    if token in text:
                        found.append(f"{path.relative_to(REPO_ROOT)} contains {token}")
                if "prefix_for" in text:
                    found.append(f"{path.relative_to(REPO_ROOT)} contains prefix_for")
        self.assertEqual(found, [])
        research_text = "\n".join(
            path.read_text(encoding="utf-8") for path in RESEARCH_DIR.rglob("*.py")
        )
        self.assertNotIn("if scenario_id", research_text)
        self.assertNotIn("priority_score =", research_text)
        self.assertNotIn("weighted_score =", research_text)

    def test_gate17_execution_harness_does_not_read_hidden_truth(self) -> None:
        path = REPO_ROOT / "tests" / "e2e" / "gate17_harness.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_attrs = {
            "hidden_evaluation",
            "expected_class",
            "security_violation",
            "attempt_finding",
            "human_decision",
            "expected_max_promotion_stage",
            "forbidden_promotions",
            "required_falsified_classes",
            "expected_surviving_hypothesis_classes",
            "pair_group",
            "leakage_canary",
        }
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                found.append(f"{node.lineno}:{node.attr}")
            if isinstance(node, ast.Name) and node.id in forbidden_attrs:
                found.append(f"{node.lineno}:{node.id}")
        self.assertEqual(found, [])
        self.assertNotIn(".hidden_evaluation", source)


if __name__ == "__main__":
    unittest.main()
