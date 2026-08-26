"""Project maturity flags. These are not research conclusions and are not auto-advanced.

ARCHITECTURE_VALIDATED means Decisions 001–050 and GATE 01–13 architecture exist.

GATE 01 PASS means Zest can compile authorized program scope and policy into a
fail-closed dispatch envelope that differentiates loopback fixtures from real IN_SCOPE
targets, while keeping UNKNOWN targets observable-but-not-probed. It was validated on
Kali against dedicated PostgreSQL with the full suite (1225 passed, 9 skipped). It does
not prove autonomous vulnerability discovery, arbitrary external internet targeting,
live bug-bounty performance, platform sync to live HackerOne/Bugcrowd endpoints,
OAST callbacks, program-policy actions beyond DENY, or production readiness.

GATE 02 PASS means the Sensor/Acquisition Plane (SD-G2) is sealed at e2bf18b +
seal commit; independent architect audit: 977 unit+contract passed, boundary
clean, admission per spec. This is the Attack Period sensor plane:
passive/semi-passive external census (DNS, certificate transparency, archive,
certificate metadata, technology fingerprint) that produces SensorObservation
records marked UNTRUSTED_EXTERNAL. Sensors never write domain truth; observations
become DiscoveryFact only after deterministic admission (forbidden-key rejection,
scope provenance binding, capped at OBSERVED, admission receipt). SD-G2 is NOT the
old infrastructure GATE 02 (Bounded Research Reasoning Cycle,
a8_001_research_reasoning, closed 2026-08-16); those are separate eras and must
never be confused. GATE 02 does not prove autonomous vulnerability discovery,
active probing, live internet reconnaissance, bug-bounty performance, or production
readiness.

GATE 14 PASS means the controlled authorized local HTTP authorization-differential
pipeline E2E ran on Kali against dedicated PostgreSQL. It does not mean live models,
autonomous discovery quality, bug-bounty performance, or production readiness.

GATE 15 PASS means the controlled multi-scenario ground-truth / false-positive
benchmark passed on Kali against dedicated PostgreSQL. It does not mean live
models, autonomous discovery quality, bug-bounty performance, production
readiness, or broad security-research validation.

GATE 16 PASS means the controlled workflow/state-transition authorization
benchmark plus cross-class discrimination against HTTP_AUTHORIZATION_DIFFERENTIAL
passed on Kali against dedicated PostgreSQL. It does not mean live models,
autonomous discovery quality, bug-bounty performance, production readiness,
or broad security-research validation.

GATE 17 PASS means controlled local multi-hypothesis closed-loop research
selection and adaptive experiment choice were validated against the dedicated
real PostgreSQL test database with truth-blind benchmark execution. It does
not prove general autonomous vulnerability discovery, live model quality, or
production readiness.

GATE 18 PASS means Zest can transform an admitted research intent into
a typed, per-action capability-bound and scope-evaluated experiment whose
risk level and capability definition are independently verified by Core,
durably bound across restart, and independently rejected by the Worker if
its executable definition does not match. It does not prove autonomous
vulnerability discovery, broad security-research capability, real-world bug
bounty performance, live model quality, production readiness,
crawler/browser/recon capability, or XBOW/Edra parity.

GATE 19 PASS means Zest can construct and execute typed, capability-bound,
Core-authorized general HTTP experiments using bounded request methods, paths,
queries, headers and bodies, while preserving exact scope evaluation, redirect
reauthorization, capability fingerprint enforcement and Worker execution bounds.
It does not prove autonomous endpoint discovery, crawler/recon capability,
browser automation, arbitrary internet HTTP, broad vulnerability discovery,
real-world bug bounty performance, or production readiness.

GATE 20 PASS means Zest can establish and isolate authenticated sessions
for explicitly configured identities and execute authorized HTTP experiments
under the correct identity/session context without storing raw credential or
session material in the authoritative research state. It does not prove
autonomous account discovery, browser authentication, arbitrary authentication
mechanisms, durable session-secret recovery after restart, autonomous
vulnerability discovery, real-world bug bounty performance, or production
readiness.

GATE 21 PASS means the browser/application-state capability has been formally
qualified on the authoritative Linux host with real Chromium and kernel-enforced
containment. It does not prove autonomous discovery, crawler behavior,
bug-bounty performance, browser-based vulnerability discovery, general internet
browsing, or production readiness.

GATE 22 PASS means Zest can autonomously build and maintain a bounded,
provenance-rich, identity/state-aware attack-surface model of an authorized local
target using real Browser/HTTP observations. It does not prove autonomous
vulnerability discovery, bug-bounty capability, production readiness, generalized
internet reconnaissance, or GATE 23.

GATE 03 PASS means the Attack Period SurfaceGraph v2 (SD-G3) is sealed at
05ce3c0 + seal commit; independent architect audit: 991 unit+contract passed;
silent-drop eliminated; UNTRUSTED_EXTERNAL preserved in graph; scope provenance
mandatory. SD-G3 upgrades the AttackSurfaceGraph so sensor-derived
DiscoveryFact kinds (DOMAIN, HOSTNAME, CERT, SERVICE, TECH, JS_BUNDLE, API_SPEC)
become first-class graph citizens with provenance, scope classification, and
deterministic hash. It adds the attack_surface_snapshot table (hash + counts
only; nodes/edges remain rebuildable from the ledger). SD-G3 is NOT the old
infrastructure GATE 03 (Learning Cycle, a9_001_learning_cycle, closed
2026-08-16); those are separate eras and must never be confused. GATE 03 does
not prove autonomous vulnerability discovery, active probing, live internet
reconnaissance, bug-bounty performance, or production readiness.

GATE 04 PASS means the Attack Period Token Economy Policy (SD-G4) is sealed at
6950f28 + seal commit; independent architect audit: 1014 unit+contract passed;
routing behavior tests fully restored (20/20, 52 assertions); token economy:
pricing fail-closed, daily budget DENY, escalation reasoned, monitoring zero-call.
SD-G4 upgrades LLM economics from "counting invocations" to "managing cost":
near-zero cost in monitoring mode, cheap model as default, expensive model only
on proven escalation, and a per-program daily LLM budget ceiling enforced
fail-closed. Costs are tracked in microdollars from token consumption records;
the ledger is the single source of truth. SD-G4 is NOT the old infrastructure
GATE 04/04B (Benchmark Compatible policy); those are separate eras and must
never be confused. GATE 04 does not prove autonomous vulnerability discovery,
active probing, live internet reconnaissance, bug-bounty performance, or
production readiness.

GATE 05 PASS means the Attack Period HunterFamily Registry + First Hunt Cycle
(SD-G5) is sealed at 310993d + seal commit; independent architect audit: 1038
unit+contract passed; data-driven HunterFamily registry (5 seed families);
V1/V2/V3 tiers enforced; V3 queue approval-gated; scope confinement via
IN_SCOPE preconditions. SD-G5 replaces the old hardcoded two-family claim-string
matching with a data-driven `hunter_family` registry and runs the first hunt
cycle: surface graph → family matching → deterministic hypothesis generation →
V1 (static) → V2 (passive evidence) → V3 (active experiment queue). The registry
is append-only versioned data; LLM cannot write to it. Five seed families cover
OBJECT_AUTHORIZATION, WORKFLOW_STATE_TRANSITION, EXPOSED_API_SPEC,
UNPROTECTED_HOSTNAME, and TECH_KNOWN_CVE_SURFACE. V3 queue items are PENDING
until a separate active-experiment approval gate. SD-G5 is NOT the old
infrastructure GATE 05 (Learning Cycle); those are separate eras and must never
be confused. GATE 05 does not prove autonomous vulnerability discovery, active
probing execution, live internet reconnaissance, bug-bounty performance, or
production readiness.

GATE 06 PASS means the Attack Period Mutation Engine + OAST Core + Rate-Limit
Enforcement (SD-G6) is sealed at 5556463 + seal commit; independent architect
audit: 1076 unit+contract passed; 7 mutation families deterministic and
scope-confined; OAST core loopback-verified with stale rejection; rate-limit
enforced pre-Core; V3 enqueue hard-locked to IN_SCOPE. SD-G6 provides
deterministic attack-variant planning from observed HTTP surface nodes and an
out-of-band callback token lifecycle for blind-vulnerability proofs, while
keeping all generated variants scoped to IN_SCOPE nodes and all callbacks bound
to provenance before admission as UNTRUSTED_EXTERNAL observations. SD-G6 is NOT
the old infrastructure GATE 06 (State Transition Security benchmark); those are
separate eras and must never be confused. GATE 06 does not prove live OAST
infrastructure, autonomous exploitation execution, live internet reconnaissance,
bug-bounty performance, or production readiness.

GATE 08 PASS means the Attack Period Coverage Debt matrix (SD-G8) is sealed at
ec5e4df + seal commit; independent architect audit: 1111 unit+contract passed;
deterministic coverage debt matrix (Asset x Identity x Family); IN_SCOPE-only
debt; identity-agnostic hypothesis semantics sealed; snapshot append-only. SD-G8
computes a deterministic, LLM-free (node x identity x HunterFamily) coverage-debt
matrix from the AttackSurfaceGraph v2, the HunterFamily registry, and the
hypothesis/audit ledger. Only IN_SCOPE nodes produce debt cells;
UNKNOWN/OUT_OF_SCOPE nodes are marked NOT_APPLICABLE and do not enter the debt
count. Identity-agnostic hypotheses (the SD-G8 boundary, because HypothesisRecord
does not carry identity) are spread to all identity cells of the matching
(node, family) pair; per-identity binding is scheduled for SD-G9. The matrix
computes a SHA-256 hash over a canonical, sorted cell list so that identical
inputs always yield identical hashes. A durable but rebuildable summary is
persisted in coverage_debt_snapshot (matrix_hash + cell_counts + total_debt);
the full matrix remains reconstructible from the ledger. SD-G8 is NOT the old
infrastructure GATE 08 (Invariant Chain benchmark, a14_001_invariant_chain,
closed 2026-08-16); those are separate eras and must never be confused. GATE 08
does not prove autonomous vulnerability discovery, active probing execution,
live internet reconnaissance, bug-bounty performance, or production readiness.

GATE 09 PASS means the Attack Period HunterScore Scheduler + Identity Binding
(SD-G9) is sealed with HunterScore v2 starvation/lock-in corrections. SD-G9
closes the SD-G8 D7 boundary by adding
identity_id to HypothesisRecord and HuntV3QueueRecord, so that each hypothesis
is bound to a specific identity (or ANONYMOUS) rather than spreading
identity-agnostic state across all identity cells. GenerateHuntHypotheses now
expands node.identity_ids per family up to MAX_IDENTITIES_PER_NODE (8), emits
an IDENTITY_EXPANSION_CAPPED audit event when the cap is hit, and renders the
identity into the claim template. The RunHuntScheduler use case computes a
deterministic, LLM-free HunterScore for every (node, identity, family) debt cell
using state weight, a bounded family-success prior (supported/falsified counts
from the append-only ledger), a low-history exploration bonus, latest-activity
freshness that is distinct from first-seen audit context, and daily LLM budget
suitability (cheap-path preference when budget is exhausted). It emits a
HUNT_SCHEDULE_RECOMMENDED audit event with the ranked top N cells. RunHuntCycle
can consume this schedule and still enforces the V1/V2/V3 tier gates and the
IN_SCOPE V3 enqueue lock. Seal validation: 1450 pytest tests passed, 9 skipped,
44 warnings, 53 subtests passed on real PostgreSQL integration/e2e coverage.
SD-G9 is NOT the old
infrastructure GATE 09; those are separate eras and must never be confused.
GATE 09 does not prove autonomous vulnerability discovery, active probing
execution, live internet reconnaissance, bug-bounty performance, or production
readiness.

GATE 10 PASS means the Attack Period Independent Validator, Severity Engine,
and Circuit Breaker (SD-G10) is sealed. SD-G10 is not the old
infrastructure GATE 10 (Runtime / Strix Boundary Integrity). The SD-G10
validator rejects admission when required V1/V2/V3 tiers are missing or not
passed; V3 QUEUED is not a validator PASS. FindingProposal admission fails
closed without append-only validator evidence. The severity engine is
downstream of validation and IN_SCOPE state, maps internal P0-P3 to
platform-style Bugcrowd/HackerOne labels, persists only audit decisions, and
does not leak severity/confidence/finding language into Hypothesis,
Observation, Evidence, Candidate, or early FindingProposal records. The family
circuit breaker uses append-only telemetry to throttle noisy families without
disabling or deleting them. Seal validation: PostgreSQL SD-G10 integration
scenarios cover V1/V2 missing, V3 QUEUED, deterministic severity scoring,
out-of-scope/validation-missing non-scoring, and throttle-without-disable
telemetry. GATE 10 does not prove live internet reconnaissance, production
executor depth, bug-bounty performance, or production readiness.

GATE 07 PASS means the Attack Period ImpactGraph (SD-G7) is sealed at a1d8e39 +
seal commit; independent architect audit: 1100 unit+contract passed;
ImpactGraph chains proof-backed, cross-run locked, impact kinds bounded by
demonstrated capabilities (composite ATO rule); admission rejects chainless
impact claims. SD-G7 binds every impact claim in a FindingProposal to a
proof-supported ImpactChain: each ImpactNode references ledger-resolvable
proof_ids (evidence/observation/experiment), its impact_kind must stay within
the demonstrated capabilities of those proofs, and the chain must pass
structural validation (acyclic, no dangling edges, no empty proofs, no
hallucinated sources, no cross-run proofs). RegisterImpactChain persists chains
append-only; SubmitFindingProposal validates them at admission time and rejects
any proposal with IMPACT_CHAIN_MISSING, IMPACT_CHAIN_CROSS_RUN, or
IMPACT_EXCEEDS_DEMONSTRATED_CAPABILITY. SD-G7 is NOT the old infrastructure
GATE 07 (Target Differential benchmark); those are separate eras and must never
be confused. GATE 07 does not prove autonomous vulnerability discovery, active
exploitation execution, live internet reconnaissance, bug-bounty performance, or
production readiness.

PRODUCTION_READY must stay false until operational and live-research gates
that this environment has not passed actually pass.
"""

from __future__ import annotations

ARCHITECTURE_VALIDATED = True
DIAGNOSTIC_E2E_VALIDATED = True
LIVE_MODEL_VALIDATED = True
SECURITY_RESEARCH_VALIDATED = False
PRODUCTION_READY = False

GATE_01_STATUS = "PASS"
GATE_02_STATUS = "PASS"
GATE_03_STATUS = "PASS"
GATE_04_STATUS = "PASS"
GATE_05_STATUS = "PASS"
GATE_06_STATUS = "PASS"
GATE_07_STATUS = "PASS"
GATE_08_STATUS = "PASS"
GATE_09_STATUS = "PASS"
GATE_10_STATUS = "PASS"
GATE_12_STATUS = "PASS"
GATE_13_STATUS = "PASS"
GATE_14_STATUS = "PASS"
GATE_15_STATUS = "PASS"
GATE_16_STATUS = "PASS"
GATE_17_STATUS = "PASS"
GATE_18_STATUS = "PASS"
GATE_19_STATUS = "PASS"
GATE_20_STATUS = "PASS"
GATE_21_STATUS = "PASS"
GATE_22_STATUS = "PASS"
GATE_04B_STATUS = "PASS"
SUBSCRIPTION_OAUTH_STATUS = "NOT_IMPLEMENTED"


def maturity_mapping() -> dict[str, object]:
    return {
        "ARCHITECTURE_VALIDATED": ARCHITECTURE_VALIDATED,
        "DIAGNOSTIC_E2E_VALIDATED": DIAGNOSTIC_E2E_VALIDATED,
        "LIVE_MODEL_VALIDATED": LIVE_MODEL_VALIDATED,
        "SECURITY_RESEARCH_VALIDATED": SECURITY_RESEARCH_VALIDATED,
        "PRODUCTION_READY": PRODUCTION_READY,
        "GATE_01": GATE_01_STATUS,
        "GATE_02": GATE_02_STATUS,
        "GATE_03": GATE_03_STATUS,
        "GATE_04": GATE_04_STATUS,
        "GATE_04B": GATE_04B_STATUS,
        "GATE_05": GATE_05_STATUS,
        "GATE_06": GATE_06_STATUS,
        "GATE_07": GATE_07_STATUS,
        "GATE_08": GATE_08_STATUS,
        "GATE_09": GATE_09_STATUS,
        "GATE_10": GATE_10_STATUS,
        "GATE_12": GATE_12_STATUS,
        "GATE_13": GATE_13_STATUS,
        "GATE_14": GATE_14_STATUS,
        "GATE_15": GATE_15_STATUS,
        "GATE_16": GATE_16_STATUS,
        "GATE_17": GATE_17_STATUS,
        "GATE_18": GATE_18_STATUS,
        "GATE_19": GATE_19_STATUS,
        "GATE_20": GATE_20_STATUS,
        "GATE_21": GATE_21_STATUS,
        "GATE_22": GATE_22_STATUS,
        "SUBSCRIPTION_OAUTH": SUBSCRIPTION_OAUTH_STATUS,
        "contains_secrets": False,
    }
