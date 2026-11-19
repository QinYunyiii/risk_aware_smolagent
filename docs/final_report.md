# Risk-Aware smolagents / MiniToolBench-R Final Engineering Report

## Executive Summary

The 14-day implementation produced an end-to-end engineering prototype for risk-aware tool use with local experiment control and remote model inference. The project includes four deterministic task domains, fault injection, structured trajectories, task-level Checkpoint/Restore, isolated intervention branches, risk and utility models, a safety-constrained adaptive policy, baseline/ablation evaluation, and a read-only Recovery-Bench Adapter.

The engineering gates pass. The research evidence remains preliminary: the learned-policy experiments use 20 Train-only checkpoints from controlled local reference scenarios, not an independent Validation/Test distribution. Results therefore demonstrate system integration and data lineage, not generalizable causal or model-quality claims.

## Architecture

```text
Local WSL experiment controller
├── smolagents orchestration
├── local tools and deterministic verifiers
├── faults, monitoring and budgets
├── Checkpoint / Restore / Branch Runner
├── risk, utility and adaptive policy
├── experiment artifacts and reports
└── read-only Recovery-Bench Adapter
             │
             │ strict TLS over SSH tunnel
             ▼
Remote shared server
├── independent vLLM Worker 0 on one authorized idle RTX 4090
└── independent vLLM Worker 1 on another authorized idle RTX 4090
```

The model returns text or tool calls. Tool calls execute locally; the remote model cannot directly access local Docker, files or databases.

## Delivered Components

| Area | Delivered capability |
|---|---|
| Remote inference | Two independent Qwen3-8B/vLLM workers, loopback binding, API key, TLS verification and SSH tunnels |
| Agent integration | Explicit/round-robin routing, worker identity, local smolagents tools and service-error taxonomy |
| Benchmark | 40 templates, 200 deterministic instances across four domains, grouped 24/8/8 template split |
| Faults | F0–F6, deterministic seeds, commit/response semantics and Retry safety guard |
| Monitoring | Append-only tool trajectories, tokens, latency, errors, versions, budgets and secret redaction |
| State | Files, SQLite Backup API, Mock API state, Agent memory, tool state, history, budget, config and normalized hashes |
| Branching | Independent Continue/Retry/Replan restores, applicability reasons, state hashes, validation and costs |
| Risk | Leakage-controlled 26-feature Logistic Regression and LightGBM pipelines |
| Utility | Per-action outcome models and per-action/per-component nonnegative cost models |
| Policy | Risk gate, utility/cost comparison, safety mask, budget, loop limits and safe fallback |
| Evaluation | Five baselines, four ablations, paired comparison and domain/fault stratification |
| Recovery-Bench | Read-only Harbor 0.4.0 / ATIF-v1.6 Adapter with checksum and schema validation |

## Key Engineering Results

### Remote and Agent Integration

- Day 2: Worker 0 completed 13 and Worker 1 completed 6 real conversations.
- Eleven real tool calls executed locally; all 11 tool executions succeeded and nine full Agent runs succeeded.
- TLS error, unavailable service and task/tool failure were classified separately.
- Day 8: 24 Train tasks produced 43 append-only attempts and 26 real local tool calls across both workers and all four domains.
- Logical outcomes were 19 success, four infrastructure failures and one task failure; 22/24 deterministic validations passed.
- Task-log completeness was 99.28% and tool-log completeness 95.24%.
- Remote workers, tunnels and GPU allocations were stopped and released after validation.

### Tasks, Faults and State

- Forty templates generated 200 unique instances, 50 per domain; all reference executions and reset checks passed.
- F0–F6 fault scenarios passed deterministic reproducibility and Retry-safety checks.
- Checkpoint payloads cover local files, SQLite, Mock API state, Agent memory, history, tool state, budgets, task config and seed.
- Restore reproduces the normalized snapshot hash; corruption, symlinks, overwrite and identity mismatch are rejected.
- Isolated branch tests prevent shared mutable state and record action applicability and costs.

### Risk and Utility

Risk evaluation uses 20 Train checkpoints with template-grouped out-of-fold predictions:

| Model | AUROC | AUPRC | Brier | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| Logistic Regression | 1.000 | 1.000 | 0.011966 | 1.000 | 1.000 |
| LightGBM | 1.000 | 1.000 | 0.004261 | 1.000 | 1.000 |

These near-perfect results mainly reflect controlled scenario construction and a highly separating feature. They are not independent Test performance.

Utility training contains 36 applicable outcomes from 60 action records. Continue has both classes; all six Retry and all ten Replan examples succeed, so those actions require a Beta-smoothed constant outcome fallback. Aggregate applicable-action Outcome Brier is 0.013236, Utility MAE is 0.064500 and mean observed-action Policy Regret is 0.005003.

### Adaptive Policy and Day 13 Evaluation

The Day 12 adaptive policy completes 20/20 local Checkpoint decisions and executions, with 11 Continue and nine Replan decisions. All restores and deterministic validations pass, and eight safety/budget/fallback probes pass.

Day 13 evaluates nine policies over the same 20 Train checkpoints:

| Policy | Success | Recovery | Intervention | Unnecessary / Intervention | Mean Regret |
|---|---:|---:|---:|---:|---:|
| Vanilla | 55% | 0% | 0% | 0% | 0.45000181 |
| Always Retry | 55% | 0% | 30% | 100% | 0.45000084 |
| Rule-based | 100% | 100% | 75% | 40% | 0.00500237 |
| Risk-based | 100% | 100% | 45% | 0% | 0.00000099 |
| Risk + Utility / Full | 100% | 100% | 45% | 0% | 0.00000099 |

Full improves paired success by 45 percentage points over Vanilla and intervenes 30 percentage points less than Rule-based. Risk-based, Risk + Utility, Full and all three non-Full ablations choose identical actions on this small, separable dataset. The experiment therefore does not establish an incremental benefit from risk, utility or cost components.

## Recovery-Bench Extension

The final Adapter reads existing Harbor artifacts without importing or modifying the external repository. Three checksum-pinned trials cover a success, a verifier failure and an AgentTimeout:

- 37 ATIF steps;
- 100 tool calls;
- 34 observation results;
- config/result consistency and verifier reward files validated;
- source file SHA-256 values unchanged before and after parsing;
- raw historical arguments and observations not copied by the formal Demo.

Twenty-three observations cannot be reliably linked to one tool call because the source ATIF records lack `source_call_id` and have unequal call/result counts. They remain explicitly step-level and unlinked.

Recovery-Bench/Harbor Docker supports a fresh trial environment and command replay, but not an intermediate normalized snapshot, hash-verified Restore or isolated branches from the same checkpoint. No Recovery-Bench branch utility experiment was executed, and these trials were not added to the utility dataset.

## Research Question Assessment

| RQ | Current evidence |
|---|---|
| RQ1: predict failure risk? | The pipeline learns the controlled 20-checkpoint Train distribution, but independent generalization is untested. |
| RQ2: do interventions differ from the same state? | MiniToolBench-R produces hash-verified independent branches with different observed outcomes; coverage is small and reference-controller-specific. |
| RQ3: predict outcome and cost? | Models, constraints and metrics work end to end, but Retry/Replan lack negative examples and Token cost is zero in local branches. |
| RQ4: improve reliability–cost tradeoff? | Full improves over Vanilla and unnecessary Rule-based interventions on the same Train scenarios, but ties simpler policies and all ablations. No general causal conclusion is supported. |

## Threats to Validity

- Day 10–13 use only 20 Train checkpoints; no official Validation/Test checkpoints exist.
- Fault scenarios are controlled and easy to separate; they are not estimates of real fault prevalence.
- The branch controller is local and deterministic, not autonomous Qwen3-8B Retry/Replan behavior.
- Retry/Replan applicable samples contain no failures.
- Local branch Token cost is zero and latency is microsecond/millisecond scale, unlike remote or Docker workloads.
- Cost weights and thresholds are engineering defaults selected without official Validation tuning.
- Day 13 ablations are indistinguishable on the current data.
- Recovery-Bench historical command replay is not a consistent state restore protocol.
- The external Recovery-Bench working tree contains substantial pre-existing user modifications and remains outside this project's version control.

## Reproducibility and Data Protection

- Source configs, scripts, model metadata and daily reports are in this repository.
- Raw run directories and model binaries are ignored by Git and remain append-only locally.
- Each formal run records upstream run IDs, catalog/task hashes and schema versions.
- Secrets, private keys, raw model weights and raw Recovery-Bench contents are not included in the delivery artifacts.
- See [reproducibility.md](reproducibility.md) for commands and [demo.md](demo.md) for the final integration demonstration.

## Conclusion

The project is complete as a 14-day engineering prototype. It provides the mechanisms required for risk-aware local tool execution, controlled intervention experiments and auditable Recovery-Bench artifact reuse. The next research phase should collect larger, real-model, failure-diverse Train/Validation/Test branch data before making claims about predictive generalization, causal intervention utility or superiority over simpler policies.
