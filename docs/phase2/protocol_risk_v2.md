# Risk Data Protocol v2

> Protocol ID: `risk-continue-v2.0.0`
> B1 status: frozen on 2026-11-20 (Asia/Shanghai); implementation evidence remains subject to B2/B3 Gates.
> Scope: real Qwen3-8B `CONTINUE` risk data. RETRY/REPLAN outcome learning is out of scope.
> Evidence cutoff: repository state audited on 2026-11-20 (Asia/Shanghai).

## 1. Research estimand

At decision time \(t\), the primary estimand is

\[
r_t=P(Y_{\mathrm{continue}}=0\mid X_{\leq t}).
\]

`X_<=t` contains only Agent history, Agent-visible tool responses, and runtime information
available before the next model inference. `Y_continue=1` means the unassisted CONTINUE
branch passes the pre-registered deterministic verifier; `0` means an observable, valid
CONTINUE outcome fails it; `null` means the outcome is censored or invalid because it could
not be observed under the protocol. `null` is never converted to failure.

This protocol does not claim that the historical Day 9--13 local-reference data estimates
this quantity. The formal v2 cohort must use `controller_type=qwen3_model`,
`execution_mode=model_backed_formal`, and Checkpoint schema v2.

## 2. Decision checkpoint

### 2.1 Primary capture rule

The primary analysis point is the **first eligible non-terminal Tool Observation** in each
task attempt:

1. Qwen has produced an Assistant tool call.
2. The local tool has completed or returned an Agent-visible error.
3. The corresponding Observation/error has been added to the current smolagents
   `ActionStep` and that step has been appended to `AgentMemory`.
4. The canonical message list used for the next model inference ends in the Tool Response.
5. No next model request or next tool operation has started.
6. At least one model step remains and the event is not a `final_answer` tool call.

For `smolagents==1.26.0`, this is the public stream yield immediately after
`memory.steps.append(action_step)`. Snapshotting is synchronous; the stream must not be
advanced until snapshot completion. If an ActionStep contains parallel tool calls, capture
occurs only after every call in the step has settled and all observations are in deterministic
call-ID order. The record declares `capture_granularity=action_step_observation_batch`.

After capture, the source attempt is paused and closed with status `checkpoint_captured`.
It is not labelled success/failure. The label comes only from an independently restored
CONTINUE branch.

### 2.2 Alternatives considered

| Scheme | Independence/comparability | Coverage/value | Cost/bias | Decision |
|---|---|---|---|---|
| A. First eligible Observation | One primary row per task; consistent lifecycle position | Covers success and failure without looking at outcomes | Lowest cost; may under-represent late loops | **Primary protocol** |
| B. Fixed middle Observation | Comparable only when task lengths are known and similar | Better late-history signal | Excludes short/early-failing tasks and requires a length rule | Reject for primary |
| C. Multiple Observations | Rich temporal risk curve | Best late-error coverage | Correlated rows, more branches/cost, unequal task weighting | Separate pre-registered extension; cluster by task/template |
| D. Only after tool error | Strong error signal | Misses normal precursors and successful controls | Direct selection bias; cannot estimate general risk | Prohibited as primary sampling |

Checkpoint selection never uses final outcome, hidden fault metadata, verifier state, or
future observations. Failure-only or error-only retrospective selection is prohibited.

### 2.3 Capture boundary

Events that must already have occurred:

- Assistant model output and exact tool-call ID/name/arguments;
- local tool execution or Agent-visible tool error;
- raw tool event recording;
- exact Observation/error conversion performed by smolagents;
- ActionStep append to Agent Memory;
- construction and hashing of the next model input.

State that must be saved:

- complete canonical messages and full Agent Memory steps;
- System Prompt, Task Prompt, Assistant tool call(s), Tool Observation(s);
- `step_number`, `max_steps`, remaining budgets, Agent state and planning state;
- tool registry schemas/version, Prompt templates, model/generation configuration;
- RuntimeState, completed tool history, previous model usage/latency;
- Workspace, file bytes, SQLite backup, Mock API state, idempotency/side-effect state;
- tool/fault adapter counters and RNG state needed to reproduce future environment behavior;
- experiment/run/task/template/split/model/worker/version lineage and component hashes.

Events prohibited before capture completes:

- next `model.generate()` request;
- next tool call, state refresh, retry, replan, verifier call, or feature-label join;
- mutation of workspace/SQLite/Mock API after the captured Observation;
- any prompt or message added for intervention.

An environment snapshot alone is not an Agent Checkpoint. A v1 Checkpoint cannot be
upgraded by assertion; only a v2 payload passing the B2 Gate is valid.

## 3. CONTINUE semantics and label

CONTINUE restores the exact Agent, Runtime, and Environment state, then makes the next model
inference without adding instructions. It uses the original model/Worker, System Prompt,
Task Prompt, tool registry, generation configuration, remaining budgets, and verifier version.
The completed tool call is never replayed.

| Observed outcome | `Y_continue` | Rule |
|---|---:|---|
| All deterministic checks pass | 1 | Applies even if the final transition coincides with a budget boundary |
| Valid normal termination and verifier fails | 0 | Includes wrong answer, missing state, and partial completion |
| Agent reasoning/parsing/argument/loop error reaches frozen termination and verifier fails | 0 | Agent-origin failure |
| Max step/token/tool budget exhausted; services healthy; verifier fails | 0 | Pre-registered budget failure |
| Tool error was observed, Agent continued, final verifier passes/fails | 1/0 | Tool error does not determine the label |
| Model API/TLS/vLLM failure prevents a valid final result | null | Model-service infrastructure failure |
| Workspace/SQLite/Mock API corruption or unexpected environment outage | null | Environment infrastructure failure |
| Operator/manual cancellation | null | Preserve reason; no retrospective relabelling |
| Response/commit was initially unknown, but final state is deterministically verifiable | 1/0 | Use final verifier, not the earlier uncertainty |
| Final state cannot be consistently verified | null | Censored/invalid outcome |

Budget exhaustion is therefore failure only when it is the Agent's frozen execution limit,
infrastructure remains available, and the verifier does not pass. Service interruption that
prevents use of remaining budget is `null`, not budget failure.

## 4. Partial success and verifier contract

Success is conjunctive: every pre-registered check for the template must pass. Completing
only some operations, returning a success-looking message with the wrong final state, or
performing an irreversible write without satisfying the full task is failure. A lost response
does not itself decide the outcome: the deterministic verifier inspects the final isolated
state. If authoritative state cannot be read, the outcome is `null`.

Current `DeterministicVerifier` inputs are `TaskSpec`, `TaskEnvironment`, and candidate result;
its output is `{passed, checks}`. Current coverage is:

- Data Analysis, File Retrieval, Calculation & Logic: exact `result` equality;
- API Workflow: exact result plus selected `orders`/`inventory` SQLite fields;
- file checks are implemented but unused by the current 40-template catalog.

The current verifier is deterministic, but it does not classify Agent termination or
infrastructure failure and all current 40 templates are single-step. For v2, an outer outcome
evaluator must first establish outcome validity, then invoke the versioned verifier. New
multi-step templates require positive and negative verifier probes. Hidden `expected` content,
per-check truth, and verifier outputs are forbidden model features.

## 5. Feature policy

The authoritative field-level policy is `feature_allowlist.json`. Candidate features are not
enabled merely because their names look safe. A feature becomes deployment-enabled only when
B2/B3 evidence proves its raw source, cutoff time, canonical computation, and absence of
internal/future data. Until then it is `pending_b2_provenance` and
`allowed_for_deployment=false`.

Permanent exclusions include fault type/ID/schedule/seed/phase, hidden commit truth, controller
`last_failure`, expected answers, verifier internals/results, final task state, later messages,
future branch outcomes, and future usage/latency. An error truly present in the Tool Response
may be classified from that response; the injector's F-code may not be substituted for it.

## 6. Fault protocol

F0--F6 semantics, visible responses, recovery boundaries, verifier behavior, and safety rules
are frozen in `failure_taxonomy_v2.md`. Internal fault metadata is stored in a separate audit
artifact and is never joined into risk features. F5 must be evidenced by real repeated calls
or a real no-progress response; setting an internal counter does not create a label. F6 must
preserve `committed`, `not_committed`, and `unknown`; blind retry is prohibited for
`committed` and `unknown`.

## 7. Catalog, split, and sampling

The existing catalog is preserved byte-for-byte: 40 template IDs, original definitions and
24/8/8 split. Audit evidence shows all 200 generated instances have exactly one reference
step. They are useful for compatibility and capture tests, but do not satisfy the v2 multi-step
complexity objective.

The v2 target is:

- 80 templates total; 20 per domain;
- add 40 genuinely dependent 3--8 tool-step templates, 10 per domain;
- each domain adds 6 Train, 2 Validation, 2 Test templates;
- final split: 48/16/16 templates;
- two pre-registered instances per template: 96/32/32 task attempts, 160 total;
- one primary Checkpoint per successful source capture;
- target 96/32/32 valid Checkpoints; minimum Gate 72/24/24, total 120.

New template IDs and definitions are **PENDING** because B1 does not implement or mechanically
invent templates. Formal collection is blocked until the v2 catalog passes reference execution,
verifier probes, split checks, and a catalog hash freeze. This does not block B2 implementation
tests on existing Train/development tasks.

Split is a pure function of `template_id`. Every instance, Checkpoint, CONTINUE branch, later
intervention branch, and repeat belongs to that template's split. Test is never used for
training, feature selection, threshold/calibration, Prompt editing, fault-distribution tuning,
or hyperparameter/cost selection.

## 8. Domain x fault x split coverage design

Each split must contain F0--F6 overall. F0, F1, F2, F3, and the no-progress form of F5 should
appear in all four domains when the tool adapter can produce a faithful response. F4 requires
versioned state and is mandatory for API Workflow; other-domain F4 cells remain N/A unless a
real version contract is added. F6 is mandatory for API Workflow writes; it is N/A for current
read-only Data/File/Logic tools. Train covers all three F6 commit states. Validation/Test each
include at least two F6 cases chosen before outcome observation, one of which is `unknown`.

The primary first-Observation cohort can observe F5 no-progress, but usually cannot yet show a
repeated action count. Actual repetition is reserved for the multi-Checkpoint extension and
must be analysed with within-task clustering. The final deterministic fault schedule is frozen
with the catalog before collection and may not be changed in response to labels.

## 9. Evaluation and statistics

Required models/metrics:

- Constant Train-prevalence baseline;
- regularized Logistic Regression;
- LightGBM only if Train has at least 200 valid rows, at least 30 examples per class, and the
  effective feature count passes the pre-registered sample-size check;
- AUROC, AUPRC, Brier score, Log Loss, Precision, Recall, calibration curve and reliability
  diagram.

Train cross-validation and all resampling group by `template_id`; if multiple Checkpoints exist
per task, the whole `task_run_id` stays together. Bootstrap samples template clusters (and all
their tasks/Checkpoints), not individual rows. A single-class fold reports AUROC/AUPRC as
`undefined`. Every report includes valid `n`, success/failure counts, template count, and
missing/null outcome count. Precision/Recall thresholds are chosen on Validation or reported at
the pre-registered 0.5 threshold. Test is opened once after model, calibration and threshold
freeze.

The minimum 72/24/24 Gate supports engineering feasibility, fitting a strongly regularized
low-dimensional Logistic baseline if both classes are present, and a coarse independent check
for large effects. It does **not** guarantee statistical significance, stable calibration,
LightGBM suitability, reliable Domain x Fault subgroup estimates, or narrow confidence
intervals. The Gate is not a power calculation.

## 10. Stopping and retry rules

- A source attempt without an eligible Checkpoint is retained but does not enter the risk set.
- Safe retry of an infrastructure attempt is allowed only before any tool side effect and with
  a new attempt ID; the failed record remains append-only.
- After a write, `committed` or `unknown` state, automatic whole-task replay is prohibited.
- Any checksum/hash mismatch, cross-branch mutation, Test leakage, unversioned Prompt/tool/
  verifier change, or feature provenance failure stops the batch.
- Insufficient positive/negative labels is reported; labels are never edited and null is never
  recoded. Expansion, if approved, is Train-first and protocol-versioned.

## 11. B1 decision status

Protocol `risk-continue-v2.0.0` is formally frozen by
`protocol_freeze_record_v2.md`. This is a frozen research/data contract, not an implemented
capability. B2 may implement and test the contract but must not start formal collection. The
joint Gate and implementation evidence requirements are in `b2_b4_acceptance_protocol.md`;
the historical rc1 review result remains in `b1_protocol_report.md`.
