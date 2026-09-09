"""Phase 6 final engine matrix. Status is exact, not a rolled-up PASS."""

from __future__ import annotations

CONNECTED_EXECUTABLE = "CONNECTED_EXECUTABLE"
CONNECTED_EVAL_ONLY = "CONNECTED_EVAL_ONLY"
CONNECTED_BLOCKED_BY_AUTHORITY = "CONNECTED_BLOCKED_BY_AUTHORITY"
MISSING_PRECONDITION = "MISSING_PRECONDITION"
NOT_APPLICABLE = "NOT_APPLICABLE"
ENGINE_FAMILY_NOT_IMPLEMENTED = "ENGINE_FAMILY_NOT_IMPLEMENTED"
PHASE6_NOT_CONNECTED = 0

FINAL_ENGINE_MATRIX = (
    {
        "ENGINE": "Discovery",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "facts and frontier only",
    },
    {
        "ENGINE": "Frontier",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "handoff is not authorization",
    },
    {
        "ENGINE": "Browser",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "network events require admission before Observation",
    },
    {
        "ENGINE": "HTTP",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE for raw transaction; diagnostic echo supported",
        "LIMITATIONS": "http.transaction is observation-grade",
    },
    {
        "ENGINE": "Global Scheduler",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_APPLICABLE",
        "LIMITATIONS": "single owner",
    },
    {
        "ENGINE": "Hunter",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "family work is not a Finding",
    },
    {
        "ENGINE": "Coverage",
        "STATUS": CONNECTED_EVAL_ONLY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "debt is not coverage-complete globally",
    },
    {
        "ENGINE": "Identity",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "catalog references only",
    },
    {
        "ENGINE": "Authentication",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "session establishment is a precondition",
    },
    {
        "ENGINE": "Authorization",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "SUPPORTED_NOW",
        "LIMITATIONS": "correct deny does not promote",
    },
    {
        "ENGINE": "IDOR/BOLA",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "SUPPORTED_NOW via authorization differential",
        "LIMITATIONS": "same engine as Authorization",
    },
    {
        "ENGINE": "Workflow",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "SUPPORTED_NOW",
        "LIMITATIONS": "correct reject does not promote",
    },
    {
        "ENGINE": "Mutation",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "EVIDENCE_PIPELINE_PENDING",
        "LIMITATIONS": "assessment is not a Finding",
    },
    {
        "ENGINE": "Protocol",
        "STATUS": CONNECTED_BLOCKED_BY_AUTHORITY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "SE3 Core deny is not coverage and not Worker-executed",
    },
    {
        "ENGINE": "OAST",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "EVIDENCE_PIPELINE_PENDING",
        "LIMITATIONS": "callback is not a vulnerability",
    },
    {
        "ENGINE": "Differential",
        "STATUS": CONNECTED_EVAL_ONLY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "EVIDENCE_PIPELINE_PENDING",
        "LIMITATIONS": "noise-only is not evidence",
    },
    {
        "ENGINE": "Invariant",
        "STATUS": CONNECTED_EVAL_ONLY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "EVIDENCE_PIPELINE_PENDING",
        "LIMITATIONS": "UNKNOWN is not VIOLATED",
    },
    {
        "ENGINE": "Chain",
        "STATUS": CONNECTED_EVAL_ONLY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "EVIDENCE_PIPELINE_PENDING",
        "LIMITATIONS": "hypothesis impact is not verified",
    },
    {
        "ENGINE": "Model Context",
        "STATUS": CONNECTED_EVAL_ONLY,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "NOT_EVIDENCE",
        "LIMITATIONS": "model output is untrusted proposal",
    },
    {
        "ENGINE": "Evidence",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": False,
        "EVIDENCE": "SUPPORTED_NOW for mapped strategies",
        "LIMITATIONS": "unsupported strategies skip promotion",
    },
    {
        "ENGINE": "Candidate",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": False,
        "EVIDENCE": "from supported evidence only",
        "LIMITATIONS": "VALIDATED is not a Finding",
    },
    {
        "ENGINE": "Verification",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": True,
        "EVIDENCE": "native planner/Core/Worker",
        "LIMITATIONS": "does not broaden scope",
    },
    {
        "ENGINE": "Finding Proposal",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": False,
        "EVIDENCE": "from VALIDATED candidates",
        "LIMITATIONS": "not auto-finalized",
    },
    {
        "ENGINE": "Human Review",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": False,
        "EVIDENCE": "NOT_APPLICABLE",
        "LIMITATIONS": "required before Finding",
    },
    {
        "ENGINE": "Sensors",
        "STATUS": CONNECTED_EXECUTABLE,
        "SCHEDULER_REACHABLE": False,
        "EVIDENCE": "NOT_EVIDENCE until admission",
        "LIMITATIONS": "see sensor ownership matrix",
    },
)
