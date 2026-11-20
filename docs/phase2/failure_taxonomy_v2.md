# Failure Taxonomy and F0--F6 Protocol v2

> Taxonomy ID: `failure-taxonomy-v2.0.0`
> Status: frozen research contract; implementation evidence is pending B2/B3.

## 1. Outcome categories

`failure_category` is a provenance category, not a substitute for `Y_continue`.

| Category | Definition | Label rule |
|---|---|---|
| `none` | All verifier checks pass | 1 |
| `agent_failure` | Reasoning, parsing, planning, unsafe/incorrect arguments, loops, or Agent-origin termination | 0 if outcome valid and verifier fails |
| `tool_failure_observed` | Tool returns an error/no-progress response that Agent receives | Final verifier decides 1/0 |
| `budget_exhaustion` | Frozen Agent step/token/tool budget is exhausted while services remain healthy | Verifier pass=1, otherwise 0 |
| `verification_failure` | Valid final execution does not meet one or more deterministic checks | 0 |
| `environment_failure` | Unexpected Workspace/SQLite/Mock API corruption or unavailable task infrastructure | null unless a valid final state remains verifiable |
| `model_service_failure` | vLLM/HTTPS/TLS/request failure prevents valid continuation | null |
| `manual_abort` | Operator cancellation or unplanned external stop | null; preserve reason |

Categories are assigned by an outer outcome evaluator before/alongside the verifier. Tool
errors and Agent failures must not be collapsed into model-service or environment failures.

## 2. Fault contracts

### F0 — No Fault

- Trigger: no injector mutation; tool executes normally.
- Real behavior: normal local tool result and normal side effects.
- Agent Observation: exact normal result produced by the tool adapter.
- Recognition/recovery: no special recovery.
- Duplicate risk: ordinary write semantics still apply; idempotency remains mandatory.
- Verifier: evaluates complete task state normally.
- Internal metadata: schedule ID/seed and `triggered=false`, stored only in audit data.
- Model-visible information: normal Tool Response only.
- Safety: never inject a hidden failure flag into messages.

### F1 — Timeout

- Trigger: tool adapter/transport deadline at a pre-registered call index.
- Real behavior: read may be cancelled; write outcome is separately tracked as
  `not_committed`, `committed`, or `unknown`.
- Agent Observation: normalized timeout response. For unknown writes it says completion status
  is unknown; it never reveals hidden ground truth.
- Recognition/recovery: Agent can recognize timeout. Retry is allowed for read-only operations
  or a proven `not_committed` idempotent write. `committed`/`unknown` require state query.
- Duplicate risk: high for writes with lost responses.
- Verifier: final authoritative state/result decides; unverifiable infrastructure state is null.
- Internal metadata: F1, trigger point, deadline, actual commit status, seed.
- Allowed visible information: exact timeout text/structured status received by Agent.
- Safety: bounded retry; never replay a write solely because the response timed out.

### F2 — API 503

- Trigger: pre-execution service boundary at a fixed call index.
- Real behavior: operation is rejected before execution in the primary profile.
- Agent Observation: realistic `503 Service Unavailable`, optionally with a visible retry hint;
  no F-code or hidden schedule.
- Recognition/recovery: transient unavailable is recognizable; one bounded retry is permitted
  only when non-execution is established or the operation is read-only.
- Duplicate risk: low in the primary pre-execution profile; any ambiguous variant must be F1/F6.
- Verifier: final state decides after continued Agent behavior.
- Internal metadata: F2, call index, seed, injected phase.
- Allowed visible information: response status/message actually delivered.
- Safety: no unbounded retry or endpoint switching that repeats a completed write.

### F3 — Invalid Argument

- Trigger: tool schema/domain validation before side effects.
- Real behavior: arguments are rejected and no state changes.
- Agent Observation: field-level validation error derived from the public tool contract; it must
  not reveal the expected answer.
- Recognition/recovery: Agent can correct/replan. Retrying identical arguments is prohibited.
- Duplicate risk: none for the rejected call; a corrected write still requires idempotency.
- Verifier: evaluates the final corrected or uncorrected task state.
- Internal metadata: F3, injected/captured invalid field, schedule and seed.
- Allowed visible information: exact public validation response.
- Safety: hidden valid arguments/expected result may not be included in the response.

### F4 — Stale Observation / Version Conflict

- Trigger: pre-registered version mismatch or a stale read from versioned state.
- Real behavior: a read returns an explicitly versioned stale representation, or a conditional
  write is rejected with a conflict and does not commit.
- Agent Observation: stale/version-conflict response containing only fields the real interface
  would expose.
- Recognition/recovery: Agent must refresh/query state before acting; blind unchanged retry is
  not allowed.
- Duplicate risk: stale writes may overwrite newer state if safety checks are bypassed.
- Verifier: authoritative final version/state decides.
- Internal metadata: true version/state, stale version, trigger point and seed.
- Allowed visible information: exposed version/conflict response, not hidden current truth unless
  the public API returns it.
- Safety: mandatory version check or refresh.

### F5 — Repeated Action / No Progress

- Trigger: evidence must exist in the real trace: either the Agent emits an identical normalized
  tool call again, or the environment returns a genuine no-progress result under a fixed profile.
- Real behavior: repeated call/no-op produces no relevant state delta; it is not created by
  merely setting `repeated_action_count=1`.
- Agent Observation: real repeated result, explicit no-op, or unchanged visible state.
- Recognition/recovery: compare canonical call signatures and visible progress; repeated writes
  are blocked unless idempotent and state-confirmed.
- Duplicate risk: potentially high for writes; current primary support is no-progress. Actual
  repeated-action analysis belongs to the multi-Checkpoint extension.
- Verifier: no label is assigned at trigger time; final task state decides.
- Internal metadata: schedule/counters and environment state digests for audit only.
- Allowed visible information: call/Observation history actually present in messages.
- Safety: no synthetic label and no hidden repetition flag as a feature.

### F6 — Partial Failure

- Trigger: write operation crosses a commit boundary and response delivery is disrupted.
- Real behavior and mandatory subtypes:
  - `committed`: side effect completed; response missing/failed;
  - `not_committed`: no side effect occurred;
  - `unknown`: controller cannot establish commit state at decision time.
- Agent Observation: operation outcome uncertain or response lost; hidden subtype is not exposed.
- Recognition/recovery: Agent may query authoritative state. Direct Retry is allowed only for
  proven `not_committed` plus idempotency protection. `committed` and `unknown` prohibit blind
  Retry.
- Duplicate risk: highest category.
- Verifier: independently inspects final SQLite/Mock API state and expected idempotency effects.
  If authoritative state is unavailable, label is null.
- Internal metadata: F6 subtype, commit boundary, idempotency key, seed and actual side effect.
- Allowed visible information: uncertainty response and later state-query result actually seen.
- Safety: state query/idempotency guard is mandatory; error text must not say “after commit” or
  otherwise leak the hidden subtype.

Current code gap: `FaultInjector` supports a boolean F6 `commit` parameter only and its exception
text reveals before/after commit. `unknown` and an Agent-visible response adapter are PENDING_B2.

## 3. Coverage constraints

Legend: `R` required, `N/A` unsupported by the current real tool contract, `P` permitted only
after a versioned/stateful tool contract is implemented.

| Fault | Data Analysis | File Retrieval | API Workflow | Calculation & Logic | Split rule |
|---|---:|---:|---:|---:|---|
| F0 | R | R | R | R | each split/domain |
| F1 | R | R | R | R | each split/domain |
| F2 | R | R | R | R | each split/domain when adapter emits faithful unavailable response |
| F3 | R | R | R | R | each split/domain |
| F4 | P | P | R | P | API in each split; other gaps documented |
| F5 | R | R | R | R | no-progress in primary; repetition in extension |
| F6 | N/A | N/A | R | N/A | API in each split; Train covers all three subtypes |

Validation/Test each contain at least one `unknown` F6 and one other F6 subtype, assigned before
outcomes. Missing cells remain explicit; no fake write tool is added merely to fill the matrix.

## 4. Manual abort and replay

Manual abort never becomes failure after observing the trajectory. Emergency stop records actor,
reason and time. Safe infrastructure reattempts require no prior side effect and use a new attempt
ID. A lost response after a write is audited, not automatically replayed.
