"""Evidence strategy classification. Not a Finding and not automatic promotion."""

from __future__ import annotations

from zest.application.oast_source import OAST_CALLBACK_EVALUATION_STRATEGY
from zest.research.assessment import (
    DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
    HTTP_STATE_TRANSITION_EVALUATION_STRATEGY,
    HTTP_TRANSACTION_EVALUATION_STRATEGY,
)
from zest.research.evaluators.causal_chain import CHAIN_EVALUATION_STRATEGY
from zest.research.evaluators.controlled_differential import DIFFERENTIAL_EVALUATION_STRATEGY
from zest.research.evaluators.security_invariant import INVARIANT_EVALUATION_STRATEGY
from zest.research.evidence import SUPPORTED_EVIDENCE_STRATEGIES
from zest.research.http_authentication import HTTP_AUTHENTICATION_EVALUATION_STRATEGY
from zest.research.mutation.cell_contract import MUTATION_MATRIX_EVALUATION_STRATEGY
from zest.research.protocol.step_compile import PROTOCOL_STEP_EVALUATION_STRATEGY

SUPPORTED_NOW = "SUPPORTED_NOW"
EVIDENCE_PIPELINE_PENDING = "EVIDENCE_PIPELINE_PENDING"
NOT_EVIDENCE = "NOT_EVIDENCE"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"

EVIDENCE_STRATEGY_MAP = {
    DIAGNOSTIC_ECHO_EVALUATION_STRATEGY: {
        "class": SUPPORTED_NOW,
        "engine": "HTTP",
        "candidate": True,
        "verification": True,
        "finding": True,
        "reason": "deterministic echo match with observation provenance",
    },
    HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY: {
        "class": SUPPORTED_NOW,
        "engine": "AUTHORIZATION",
        "candidate": True,
        "verification": True,
        "finding": True,
        "reason": "own/cross object-access differential with control outcome",
    },
    HTTP_STATE_TRANSITION_EVALUATION_STRATEGY: {
        "class": SUPPORTED_NOW,
        "engine": "WORKFLOW",
        "candidate": True,
        "verification": True,
        "finding": True,
        "reason": "forbidden transition observed against workflow control",
    },
    HTTP_TRANSACTION_EVALUATION_STRATEGY: {
        "class": NOT_EVIDENCE,
        "engine": "HTTP",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "raw HTTP facts are observations, not vulnerability evidence",
    },
    HTTP_AUTHENTICATION_EVALUATION_STRATEGY: {
        "class": NOT_EVIDENCE,
        "engine": "AUTHENTICATION",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "auth success/failure is a precondition, not a vulnerability",
    },
    MUTATION_MATRIX_EVALUATION_STRATEGY: {
        "class": EVIDENCE_PIPELINE_PENDING,
        "engine": "MUTATION",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "semantic anomaly lacks durable control/evaluator proof for evidence admission",
    },
    PROTOCOL_STEP_EVALUATION_STRATEGY: {
        "class": NOT_EVIDENCE,
        "engine": "PROTOCOL",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "SE3 remains Core-denied; parser step is not coverage or evidence",
    },
    OAST_CALLBACK_EVALUATION_STRATEGY: {
        "class": EVIDENCE_PIPELINE_PENDING,
        "engine": "OAST",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "correlated callback is not a vulnerability without expected sink/vector proof",
    },
    DIFFERENTIAL_EVALUATION_STRATEGY: {
        "class": EVIDENCE_PIPELINE_PENDING,
        "engine": "DIFFERENTIAL",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "controlled signal is not yet bound to an evidence claim template",
    },
    INVARIANT_EVALUATION_STRATEGY: {
        "class": EVIDENCE_PIPELINE_PENDING,
        "engine": "INVARIANT",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "VIOLATED needs resource/identity context not yet in evidence admission",
    },
    CHAIN_EVALUATION_STRATEGY: {
        "class": EVIDENCE_PIPELINE_PENDING,
        "engine": "CHAIN",
        "candidate": False,
        "verification": False,
        "finding": False,
        "reason": "SUPPORTED linkage is not verified impact and has no evidence template",
    },
}

EVIDENCE_STRATEGY_MAP_COMPLETE = True
EVIDENCE_NOT_CONNECTED = 0
VERIFICATION_NOT_CONNECTED = 0
FINDING_PROPOSAL_NOT_CONNECTED = 0


def classify_evidence_strategy(strategy: str) -> str:
    row = EVIDENCE_STRATEGY_MAP.get(strategy)
    if row is None:
        return INSUFFICIENT_CONTEXT
    return str(row["class"])


def assert_supported_aligns_with_admission() -> None:
    supported = {
        strategy
        for strategy, row in EVIDENCE_STRATEGY_MAP.items()
        if row["class"] == SUPPORTED_NOW
    }
    if supported != set(SUPPORTED_EVIDENCE_STRATEGIES):
        raise RuntimeError("SUPPORTED_EVIDENCE_STRATEGIES drifted from EVIDENCE_STRATEGY_MAP")
