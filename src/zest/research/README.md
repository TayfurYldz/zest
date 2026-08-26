# Research

Research produces **proposals**. A7 v1 adds a bounded reasoning cycle. It is not an autonomous bug-bounty agent.

Types:

- `ResearchContext` — typed epistemic input. Not a prompt blob.
- `HypothesisProposal` / `HypothesisChallenge` — untrusted structured model output.
- `HypothesisDraft` — human-seeded statement + origin. Not fact.
- `ExperimentPlan` — proposed capability/action/target plus expected and disconfirming observations. Not authorization.
- `ExperimentFeedback` — reconstructs what happened. Not a vulnerability verdict.
- `HypothesisAssessment` — context-bound learning under one experiment. Not Hypothesis truth and not Evidence.
- `EvidenceProposal` / Evidence admission — Transition B. Not Candidate, Finding, or Verification.
- `CandidateProposal` / Candidate admission — GATE 05. OPEN is not a vulnerability. VALIDATED is not a Finding.
- `VerificationPlan` / `VerificationResult` — deterministic diagnostic verifier. Proposes a Candidate transition; does not commit Candidate state and does not create Finding.
- `FindingProposal` / Finding creation gate — GATE 06. VALIDATED Candidate is not a Finding. APPROVED is the domain view of Core Approval, not a second authority. Diagnostic plumbing is not a vulnerability.
- `TargetModelProjection` / `admit_target_inference` — GATE 07. Projection over SoR, not a second truth store. Inference never becomes OBSERVED.
- `DifferentialCase` / `DifferentialObservation` — GATE 07. Difference is not a vulnerability, Evidence, or Candidate.
- `InvariantProposal` / `admit_invariant` — GATE 08. Expected-behavior hypothesis, not a fact, ScopeRule, Evidence, or vulnerability.
- `ChainHypothesis` / `compose_diagnostic_echo_chains` — GATE 08. Explicit multi-step research composition, not an exploit graph and not a Worker dispatch.
- `ResearchOpportunity` / `select_research_opportunities` — GATE 09. Exploration/exploitation policy. Priority is not truth, authorization, or Evidence. Selection is not Core ALLOW.
- `ResearchSnapshot` / `ChangeEvent` — GATE 09. Temporal Intelligence. Change is not a vulnerability. TIME differentials require snapshot provenance.
- `ModelRuntimeIdentity` / `RuntimeOutcome` — GATE 10. Runtime classification and operational outcomes. Not a provider SDK and not process execution.
- `RuntimeSelectionDecision` / `select_runtime` — GATE 11. Role-specific routing policy. Hard filters before preference. Not a magic model score and not Core authorization.
- `OrchestrationBounds` / `next_cycle_action` — GATE 12. Bounded autonomy policy. Not execution authority and not a Finding.

Admission (`admit_hypothesis`) is Research-domain logic. Generator output is not a Hypothesis until admitted.

Research must not:

- call a Worker or subprocess
- persist via PostgreSQL
- import a provider SDK
- spawn a CLI/session process or hold OAuth/session secrets
- authorize itself
- treat model output as Observation, Evidence, or Finding
- treat a matching Observation as a verified security issue
- treat `CONTENT_POLICY_BLOCKED` as Hypothesis rejection, Evidence, or a research conclusion

Application coordinates `ProposeResearchHypothesis`, `EvaluateExperimentFeedback`, and `AdmitDiagnosticEvidence` with Data ports. Deterministic assessment for GATE 03 is `diagnostic.echo.v1` only.

Provider-neutral research-behavior evaluation lives in `zest.benchmark` and `benchmarks/research/` (Decisions 029–033). Research must not import the benchmark package or provider SDKs. Benchmark reports are not Evidence, Finding, or SoR truth. Git commit metadata for reports is collected by the benchmark script, not by Domain.

Evidence admission (`admit_evidence`) is Research-domain logic. Observation, HypothesisAssessment, and model prose are not Evidence. Application coordinates persistence. Data does not decide admission. Core does not own Evidence truth.

Candidate admission (`admit_candidate`) and Verification evaluation (`evaluate_diagnostic_verification`) are Research-domain logic. Application coordinates persistence and Worker experiments. A model cannot create or transition a Candidate. VALIDATED is not a Finding.

FindingProposal admission (`admit_finding_proposal`) and Finding creation evaluation (`evaluate_finding_creation`) are Research-domain logic. Application coordinates Human Review persistence and asks Core to evaluate the recorded Approval. Core does not decide vulnerability truth. A model or Worker cannot create an authoritative FindingProposal or Finding.

Target Model projection (`project_diagnostic_target_model`) and differential comparison (`compare_diagnostic_differential`) are Research-domain logic. Core and Worker are unaware of them. A model cannot mark inference as OBSERVED.

Invariant admission (`admit_invariant`) and bounded chain composition (`compose_diagnostic_echo_chains`) are Research-domain logic. An invariant cannot become OBSERVED or alter Core scope. A chain cannot execute tools or bypass Core.

Opportunity selection (`select_research_opportunities`) and temporal comparison (`capture_diagnostic_snapshot` / `compare_diagnostic_snapshots`) are Research-domain logic. Selection cannot dispatch a Worker or alter Core authorization. A ChangeEvent cannot become Evidence, Candidate, or Finding. Snapshot retention/compaction is deferred and must never delete Evidence/Verification/Finding provenance.

`ModelPort` remains provider-neutral. Runtime kinds (`API`, `SUBSCRIPTION_OAUTH`, `CLI_SESSION`, `LOCAL_MODEL`, `EXTERNAL_AGENT`) and `INFERENCE_RUNTIME` vs `AGENT_RUNTIME` are Research classifications. Concrete adapters, argv execution, OAuth, and local transports live outside Research. Codex CLI is an agent runtime, not an ordinary inference-only ModelPort. Strix is not Research Brain.

`select_runtime` is Research-domain policy. It does not invoke a provider, spawn a CLI, or let a model choose itself. `CONTENT_POLICY_BLOCKED` does not trigger a safeguard-bypass fallback loop. Agent runtimes are not selected for inference-only roles unless explicitly allowed with a restricted capability set.
