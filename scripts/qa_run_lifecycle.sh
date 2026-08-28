#!/usr/bin/env bash
set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
    pwd
)"

cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "QA_PYTHON_NOT_FOUND=$PYTHON"
    exit 1
fi

assert_zero_skip() {
    local xml="$1"

    "$PYTHON" - "$xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1]

root = ET.parse(path).getroot()

tests = len(root.findall(".//testcase"))
failures = len(root.findall(".//failure"))
errors = len(root.findall(".//error"))
skipped = len(root.findall(".//skipped"))

print("JUNIT_TESTS=", tests)
print("JUNIT_FAILURES=", failures)
print("JUNIT_ERRORS=", errors)
print("JUNIT_SKIPPED=", skipped)

if tests == 0:
    raise SystemExit("QA_ZERO_TESTS")

if failures:
    raise SystemExit("QA_FAILURES_PRESENT")

if errors:
    raise SystemExit("QA_ERRORS_PRESENT")

if skipped:
    raise SystemExit(
        f"QA_SKIPS_NOT_ALLOWED={skipped}"
    )
PY
}

run_strict() {
    local label="$1"
    local xml="$2"
    shift 2

    rm -f "$xml"

    echo
    echo "===== ${label} ====="

    "$PYTHON" -m pytest -q \
        --junitxml="$xml" \
        "$@"

    assert_zero_skip "$xml"

    echo "${label}=PASS"
}

run_strict \
    "QA_A_CONTRACT" \
    /tmp/zest-qa-a.xml \
    tests/qa/test_run_lifecycle_contract.py

run_strict \
    "QA_B_START_TO_STOP" \
    /tmp/zest-qa-b.xml \
    tests/unit/research/test_orchestration.py \
    tests/unit/application/test_autonomous_research_controller.py \
    tests/unit/application/test_local_run_supervisor.py \
    tests/unit/application/test_orchestration_recovery.py \
    tests/unit/application/test_execute_planned_experiment.py \
    tests/unit/application/test_preflight.py \
    tests/unit/application/test_runtime_outcomes.py \
    tests/unit/application/test_research_run_control.py \
    tests/unit/application/test_zestd.py

run_strict \
    "QA_C_POLICY_SCOPE_DISCOVERY" \
    /tmp/zest-qa-c.xml \
    tests/unit/application/test_discovery_surface.py \
    tests/unit/application/test_program_research_context.py \
    tests/unit/application/test_http_transaction.py

run_strict \
    "QA_D_DURABILITY" \
    /tmp/zest-qa-d.xml \
    tests/integration/test_orchestration_terminal_immutability.py \
    tests/integration/test_orchestration_lease.py \
    tests/integration/test_mr5_durability_seal.py

run_strict \
    "QA_E_OPERATOR_RUNTIME" \
    /tmp/zest-qa-e-operator.xml \
    tests/integration/test_gate13.py \
    tests/integration/test_operator_staging.py \
    tests/integration/test_zestd.py \
    tests/integration/test_zestd_sigterm.py

run_strict \
    "QA_E1_REAL_BROWSER" \
    /tmp/zest-qa-e1.xml \
    tests/e2e/test_gate21_browser_page.py

run_strict \
    "QA_E2_FULL_SURFACE_DISCOVERY" \
    /tmp/zest-qa-e2.xml \
    tests/e2e/test_gate22_surface_discovery.py

echo
echo "RUN_LIFECYCLE_QA=PASS"
