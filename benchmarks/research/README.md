# Research benchmark (engineering data)

This directory is **evaluation data**. It is not:

- Domain SoR
- Research Memory
- production configuration
- a Worker contract
- Evidence, Finding, Candidate, or Hypothesis truth

JSON fixtures are intentional. YAML is not used.

## Visible vs hidden

Each scenario splits:

| Field | Who may see it |
|---|---|
| `visible_input` | ResearchContext, Generator, Falsifier, ModelCallRequest |
| `hidden_evaluation` | evaluator / tests / scorecard only |

Hidden evaluation **must never** enter prompts, ResearchContext, model requests, or model-visible metadata.

If hidden material is serialized into a model-visible structure, that is a **benchmark leakage failure**, not a grading footnote.

## Identity

`scenario_id` + `version` is the identity. Changing hidden expected semantics materially requires a new version. Do not silently rewrite history after results exist.

## Development / calibration / sealed holdout

| Split | Rule |
|---|---|
| `development` | Visible to developers and coding assistants. Used to debug the harness. The shipped fixtures live here. **Not** an unseen holdout. |
| `calibration` | May be inspected at defined milestones. Once used for tuning, it is not holdout. Load with `--include-calibration`. |
| `sealed_holdout` | Must live **outside** the repository tree used by Cursor/development agents. Load only from `--sealed-holdout-path` or `ZEST_BENCHMARK_HOLDOUT_PATH`. |

In-repo `--include-holdout` is rejected. A file the development agent can read is not a statistically clean holdout. Encryption with a key in the same workspace is also not clean.

Reports may include a holdout **manifest/fingerprint** (suite id, version, count, fingerprint). They must not print hidden evaluator contents.

Missing sealed holdout is **unavailable**, not a fake PASS.

## Metamorphic variants

Development variants exist for prompt-injection paraphrase, prior-hypothesis id rename, and differential reorder/rename. Surface form changes; hidden admission family stays compatible. Exact prose equality is not required.

## Scoring

The harness prints a **scorecard**: hard-fail **occurrence fractions** (for example `HALLUCINATED_SOURCE_REFERENCE: 1/5`), admission distributions, and quality dimensions.

It does **not** emit `MODEL_SCORE`, `WINNER: MODEL X`, or a success percentage that hides a hard fail.

Single-run results are not an authoritative real-model comparison. Default `--runs-per-scenario` is 3.

Latency/tokens/cost fields exist on the report and stay unset until a real adapter provides them. They are not fabricated.

## Human-review rubric (future, no UI)

Automatic checks cannot resolve every dimension. Blind human review, when added later, uses ordinal labels only:

| Dimension | Labels |
|---|---|
| usefulness | POOR / ACCEPTABLE / STRONG |
| non-triviality | POOR / ACCEPTABLE / STRONG |
| quality of alternative explanation | POOR / ACCEPTABLE / STRONG |
| experiment discrimination | POOR / ACCEPTABLE / STRONG |
| target-specific reasoning | POOR / ACCEPTABLE / STRONG |

No dashboard in GATE 04A. No LLM-as-judge in GATE 04A.

## Categories (synthetic / controlled)

No exploit payloads. No real bug-bounty targets.

1. `CLEAN_DIAGNOSTIC` — echo-style neutral baseline
2. `UNSUPPORTED_SECURITY_CLAIM` — context does not support a security conclusion
3. `AMBIGUOUS_BEHAVIOR` — multiple benign explanations
4. `SOURCE_HALLUCINATION_TRAP` — limited ids; fabricated refs must be caught
5. `PROMPT_INJECTION_CONTENT` — untrusted “ignore previous instructions…” remains data
6. `PRIOR_HYPOTHESIS_POISONING` — a prior Hypothesis is not a fact
7. `NEGATIVE_CONTEXT_SCOPE` — contradiction under context C is not “impossible everywhere”
8. `DIFFERENTIAL_REASONING_SEED` — actor/state difference is not a vulnerability
9. `INCONCLUSIVE_CONTEXT` — insufficient information; caution is desirable
10. `POLICY_BOUNDARY_TRAP` — proposal must not acquire authority

Not every scenario should be `ADMITTED`. A suite that always admits is wrong.

## Run

```
uv run python scripts/run_research_benchmark.py
uv run python scripts/run_research_benchmark.py --baseline BAD_HALLUCINATOR --runs-per-scenario 3
uv run python scripts/run_research_benchmark.py --compare-baseline GENERIC_TEMPLATE_BASELINE
uv run python scripts/run_research_benchmark.py --sealed-holdout-path D:\sealed-research-holdout
uv run python scripts/run_research_benchmark.py --write-results
uv run python scripts/run_research_benchmark.py --adapter openai --model "$env:ZEST_OPENAI_MODEL"
uv run python scripts/run_research_benchmark.py --discover
uv run python scripts/run_research_benchmark.py --discover-and-compare --runs-per-scenario 3
```

Scripted baselines are test doubles, not real models. Live adapters live in `integrations/models/`. Missing SDK, API key, or CLI session is **UNAVAILABLE**, not a benchmark failure. GATE 04B comparative PASS requires at least two real comparable runtime configurations (for example OpenAI API vs Codex CLI vs local model) executed on the same comparable suite, prompt, and evaluator versions. Runtime identity is recorded separately from provider API identity. Development-suite comparison is not unseen generalization. No `WINNER` line.
