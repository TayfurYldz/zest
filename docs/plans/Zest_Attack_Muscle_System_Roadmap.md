# Zest — Attack Muscle System Roadmap

**Date:** 2026-08-20
**Status:** derived architecture roadmap
**Evidence base:** current HEAD after SD-G9 implementation, `Zest_Global_Competitive_Intelligence_2026-08-20.md`, `Zest_Saldiri_Donemi_Entegrasyon_Plani.md`, root `OPERATIONS.md`, and `maturity.py`.

## Doctrine

The Attack Muscle System is the execution organ of Zest. It is not a scanner, not a payload dump, and not a bypass around Core. It is the set of bounded, capability-scoped, evidence-producing execution muscles that let Zest act with full force inside authorized scope.

We keep the Hippocratic invariant:

- Core owns scope, policy, budget, approval, side-effect level, and capability authorization.
- Workers are the only side-effect layer and remain untrusted.
- Every attack action is an Experiment compiled from an authorized intent.
- Evidence is admitted only through the Research pipeline.
- Findings require independent verification and human acceptance.
- Scope safety is never used as an excuse to weaken in-scope capability.

In short: **no out-of-scope movement, no diluted in-scope movement.**

## Current Placement

The pieces already exist across the plan, but they are spread across gate names. We should treat them as one organ so future work does not become a collection of wrappers.

| Muscle | Current plan coverage | Current state | Roadmap verdict |
|---|---|---|---|
| Mutation Engine | SD-G6 / attack-period G6 | Local deterministic core exists | Keep; expand by mechanism, not CWE spam |
| OAST / blind proof | SD-G6 / attack-period G6 | Loopback core exists | Needs production-grade service |
| HTTP/browser execution | G19/G21/G22 + worker runtime | Lab-oriented, bounded | Needs production executor fabric |
| Protocol specialists | attack-period G13 | Planned | Missing as executable lanes |
| Race/temporal execution | G7/G10/G12 concepts | Weak | Needs dedicated coordinator |
| Chain execution | SD-G7 / attack-period G11 | ImpactGraph exists | Needs stepwise chain executor |
| Semantic attack model | G5/G7/G8/G16 concepts | Under-modeled | Highest-priority missing brain-to-muscle bridge |
| Validator escape science | G10 concepts | Partial | Needs false-negative and escape metrics |
| Source-to-runtime proof | future/source lane | Conceptual | Needs narrow vertical slice |
| Mobile/API client lane | G2/G8/G12 ideas | Missing | P2 after web executor strength |
| Portfolio targeting | SD-G8/SD-G9 | HunterScore v1 exists | Needs HunterScore v2 / portfolio intelligence |

## Organ Boundary

The Attack Muscle System begins after Research has produced an experimentable hypothesis or scheduled coverage cell, and ends when an untrusted WorkerResult has been normalized into Observation/Artifact candidates.

It does not:

- decide scope;
- decide whether an action is allowed;
- promote evidence;
- write findings;
- submit reports;
- reinterpret program policy;
- automatically escalate side effects.

It does:

- transform an authorized capability into concrete protocol/browser/OAST/race/source-runtime execution;
- preserve correlation, replay, rate, identity, and provenance;
- produce exact artifacts for independent validation;
- make in-scope attack depth stronger without weakening the guardrails.

## Muscle Inventory

### 1. Production Executor Fabric

This is the weakest major muscle today. The project has good local HTTP/browser execution, but not enough production-depth execution to compete with XBOW-class systems.

Required capabilities:

- real HTTPS, redirects, cookies, CORS, content negotiation, compression, and proxy behavior;
- stable browser profiles, storage state, downloads, dialogs, popups, iframes, service workers, and SPA navigation;
- OAuth/OIDC/SAML/SSO session continuity through `SecretRef` and runtime-owned sessions;
- GraphQL, WebSocket, gRPC, SOAP, multipart, file upload, and streaming request execution;
- per-experiment capability token and authorized network envelope enforcement;
- deterministic replay package with request/response/screenshot/trace artifacts.

Acceptance standard:

- no worker can reach a host/path outside the Core-issued envelope;
- every redirect is reauthorized;
- every identity-bound action preserves identity/session provenance;
- every result is replayable or explicitly marked environment-sensitive;
- production executor tests include secure, vulnerable, deceptive, and scope-escape fixtures.

### 2. Semantic Attack Model

This is the missing bridge between the Research Brain and the Attack Muscle System. Without it, the system can detect response differences but cannot reliably decide what should be true in a business workflow.

Required concepts:

- actor, owner, delegate, approver, tenant, organization, entitlement, quota, monetary value, irreversible action;
- expected role/resource/action/state relationships;
- UI/API parity expectations;
- ownership and tenant-bound object graph;
- business value and crown-jewel tags;
- normal-behavior claims with uncertainty, not evidence.

Acceptance standard:

- semantic claims remain HYPOTHESIZED or INFERRED until runtime proof;
- every norm claim has at least one counterfactual experiment candidate;
- authorization and workflow hunters consume semantic relations but cannot treat them as truth;
- hidden business-flow benchmark measures precision and human disagreement.

### 3. Protocol Specialist Lanes

Generic mutation is not enough for modern bug bounty. Each high-value protocol class needs its own executor semantics, validator, and negative controls.

Priority lanes:

- GraphQL resolver and object authorization;
- WebSocket state machine and cross-session message authorization;
- OAuth/OIDC/SAML token, issuer, audience, redirect, RelayState, and session binding;
- multipart/file upload parser pipelines and content transformation;
- cache poisoning/deception and proxy normalization;
- request smuggling/desync where the deployment surface supports it;
- JWT/JWK/key-rotation and claim confusion;
- gRPC/SOAP where API_SPEC or traffic observations justify it.

Acceptance standard:

- no protocol lane runs unless SurfaceGraph evidence supports the protocol;
- each lane defines preconditions, safe negative controls, and proof artifacts;
- protocol parser differences are recorded as hypotheses, not findings;
- active side effects re-enter Core per step.

### 4. Production OAST Service

Loopback OAST proves the domain model, not field capability. Blind classes need a production-grade callback organ.

Required capabilities:

- DNS, HTTP(S), SMTP, and LDAP callback channels;
- tenant/run/experiment correlation identifiers;
- callback deduplication, expiry, replay protection, and stale rejection;
- callback admission into untrusted observations before evidence promotion;
- abuse controls, rate limits, retention policy, and secret minimization.

Acceptance standard:

- forged callback tokens are rejected;
- stale callback tokens are audited but not admitted as valid proof;
- callback artifacts cannot leak between tenants/runs;
- blind SSRF/XXE/XSS fixtures require correlated callback evidence.

### 5. Race And Temporal Coordinator

Race bugs are not proven by one deterministic replay. They require controlled probability, timing, and independent read-back.

Required capabilities:

- barrier-synchronized parallel request batches;
- jitter and retry strategy under Core budget;
- separate-session read-back;
- time-window and confidence recording;
- rate-limit and irreversible-action guardrails;
- equivalent causal-effect verification instead of byte-identical replay only.

Acceptance standard:

- every race attempt has a predeclared side-effect budget;
- every state-changing signal requires independent read-back;
- probabilistic proof records attempt count, success count, confidence, and negative controls;
- destructive or irreversible operations require explicit human approval.

### 6. Stepwise Chain Executor

ImpactGraph is the proof graph. The chain executor is the muscle that safely tests whether one primitive can feed the next.

Required capabilities:

- primitive precondition/postcondition contracts;
- capability transfer between steps: read, write, execute, token, URL control, callback, identity/session;
- per-step Core authorization and side-effect escalation;
- safe chain minimization and replay bundle;
- terminal impact stopping rules.

Acceptance standard:

- no chain edge without proof reference;
- no single approval can tunnel all future chain steps;
- every step records what capability it produced and what next step consumed it;
- chain replay can stop at safe proof without increasing harm.

### 7. Validator Escape Science

The project already fights false positives. The next risk is false negatives: real vulnerabilities rejected by overly narrow validators.

Required capabilities:

- validator escape corpus;
- challenger and arbiter roles using different models or deterministic representations;
- equivalence classes for proof, not only byte-identical replay;
- environment-sensitive and probabilistic finding semantics;
- human rejection reason taxonomy.

Acceptance standard:

- each high-value class tracks precision and estimated false-negative/escape rate;
- validators are tested against deceptive and validator-evasion fixtures;
- failed validation is context-bound negative knowledge, not global truth;
- reportability remains human-gated.

### 8. Source-To-Runtime Proof Muscle

Source should suggest; runtime must prove. This lane is a future moat, but it must not turn static findings into evidence by shortcut.

Required capabilities:

- source snapshot provenance and deployment fingerprint;
- route/controller/middleware/auth/ORM/UI relation extraction;
- source-to-sink and source-to-state hypotheses;
- runtime binding to observed SurfaceGraph nodes;
- controlled experiment generation from source claims.

Acceptance standard:

- static analysis creates hypotheses only;
- deployment mismatch blocks promotion;
- runtime proof is mandatory for Evidence;
- source-derived negative knowledge is tied to source version and build fingerprint.

### 9. Mobile/API Client Lane

This is a P2 muscle after web executor strength improves. Mobile clients reveal backend surface that web recon often misses.

Required capabilities:

- APK/IPA artifact intake;
- endpoint, deep-link, WebView, and mobile-only API extraction;
- client config and secret classification;
- optional dynamic instrumentation in authorized lab settings;
- backend relation graph linking mobile calls to API objects.

Acceptance standard:

- public client identifiers are not reported as secrets;
- active key proof requires scoped permission enumeration;
- mobile findings must bind to backend runtime impact;
- device/instrumentation artifacts stay outside authoritative truth until admitted.

### 10. Portfolio Attack Muscle

This muscle decides where force goes. It is the part of the attack system that prevents wasted compute and duplicate-heavy hunting.

Required capabilities:

- asset value and crown-jewel weighting;
- duplicate/crowding probability;
- clone/same-code grouping;
- launch/change timing;
- expected information gain;
- expected valid impact per cost/hour;
- human review queue pressure.

Acceptance standard:

- HunterScore cannot starve novel or low-history families;
- first_seen and latest_change are separate signals;
- family success prior is bounded;
- scheduler produces explanations and starvation tests;
- portfolio decisions remain recommendations, not authorization.

## SD-G9 Seal Impact

The competitive intelligence report identified two concrete SD-G9 risks:

1. unbounded family-success bonus can dominate coverage-state and novelty;
2. freshness currently uses earliest sensor observation, which misses recent change on old assets.

SD-G9 is now sealed after HunterScore v2 addressed those risks. This was not a new gate and not scope creep; it was a correction to the gate's own quality claim.

Completed SD-G9 seal adjustments:

- bounded family-success prior;
- low-history exploration bonus so novel families are not starved;
- separate `first_seen_at` from latest activity freshness;
- starvation and lock-in regression tests;
- deterministic tie-break and explanation output retained.

## Recommended Gate Placement

The Attack Muscle System should not replace the SD-G series. It should become the cross-cutting organ view that prevents muscle work from being scattered.

| Work package | Gate placement | Reason |
|---|---|---|
| HunterScore v2 correction | SD-G9 seal blocker | Current G9 quality risk |
| Production executor vertical slice | Next major gate after SD-G9 | Lab-to-field capability gap |
| Semantic World Model slice | Parallel design, then next Research Brain gate | Needed for business logic and IDOR precision |
| Production OAST | After executor slice or paired with blind class gate | Required for blind proof |
| Validator escape science | Before broad injection expansion | Precision without recall is incomplete |
| Protocol specialist lanes | After executor/OAST foundation | Each protocol needs real executor semantics |
| Race coordinator | After executor foundation | Needs timing, rate, and read-back infrastructure |
| Source-to-runtime slice | After executor + semantic foundation | Source suggestions need runtime proof |
| Mobile/API client lane | P2 after web/API field strength | Valuable but not first bottleneck |

## What We Should Refuse

- Do not add a large list of CWE families before executor and semantic depth exist.
- Do not mark static/source output as Evidence without runtime binding.
- Do not optimize for zero false positives alone; track validator false negatives.
- Do not treat more agents as diversity unless priors, tools, representations, or validators differ.
- Do not let scope safety become lower in-scope capability.
- Do not let production execution bypass the envelope, approval, rate, or evidence chain.

## Principal Recommendation

We should treat **Attack Execution Fabric / Attack Muscle System** as a first-class organ of Zest and use it to organize the next gates. The immediate engineering move is to harden SD-G9 before seal, because scheduler mistakes decide which muscles receive force. After SD-G9, the highest-value field move is a narrow production executor vertical slice paired with a Semantic World Model slice. That combination closes the biggest gap between a well-controlled Zest and an XBOW-class field competitor.
