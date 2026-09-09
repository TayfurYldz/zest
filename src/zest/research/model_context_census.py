"""Static census of model/reasoning context components. Not runtime authority."""

from __future__ import annotations

MODEL_CONTEXT_SOURCE = "CONNECTED"

MODEL_CONTEXT_COMPONENTS = (
    {
        "name": "model_runtime",
        "module": "zest.research.model_runtime",
        "caller": "BudgetEnforcedModelPort / ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "generator",
        "module": "zest.research.cycle.generate_proposal",
        "caller": "ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "falsifier",
        "module": "zest.research.cycle.generate_challenge",
        "caller": "ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "hypothesis_admission",
        "module": "zest.research.admission.admit_hypothesis",
        "caller": "ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "planner",
        "module": "zest.research.planning.plan_admitted_hypothesis",
        "caller": "ProposeResearchHypothesis then ARC/Core/Worker",
        "status": "CONNECTED",
    },
    {
        "name": "research_context_builder",
        "module": "zest.research.context.ResearchContextBuilder",
        "caller": "ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "unified_reasoning_context_packer",
        "module": "zest.application.pack_research_reasoning_context",
        "caller": "ProposeResearchHypothesis",
        "status": "CONNECTED",
    },
    {
        "name": "surface_discovery_context_pack",
        "module": "zest.research.discovery.context_pack",
        "caller": "discovery runner environmental context",
        "status": "CONNECTED",
    },
    {
        "name": "program_research_context",
        "module": "zest.application.program_research_context",
        "caller": "pack_research_reasoning_context TARGET_SCOPE/AUTHORITY",
        "status": "CONNECTED",
    },
    {
        "name": "output_contracts",
        "module": "zest.research.output_contracts",
        "caller": "cycle Generator/Falsifier",
        "status": "CONNECTED",
    },
    {
        "name": "model_reasoning_records",
        "module": "zest.data.records.ResearchReasoningRecord",
        "caller": "ProposeResearchHypothesis persist",
        "status": "CONNECTED",
    },
    {
        "name": "model_admission_records",
        "module": "zest.data.records.ResearchAdmissionRecord",
        "caller": "ProposeResearchHypothesis persist",
        "status": "CONNECTED",
    },
)

CONNECTED_MODEL_CONTEXT_ENGINES = (
    "DISCOVERY",
    "FRONTIER",
    "HTTP",
    "BROWSER",
    "HUNTER",
    "COVERAGE",
    "IDENTITY",
    "AUTHENTICATION",
    "AUTHORIZATION",
    "WORKFLOW",
    "MUTATION",
    "PROTOCOL",
    "OAST",
    "DIFFERENTIAL",
    "INVARIANT",
    "CHAIN",
    "EVIDENCE",
    "CANDIDATE",
    "VERIFICATION",
    "FINDING",
    "COMPLETION",
    "AUTHORITY",
    "TARGET_SCOPE",
)
