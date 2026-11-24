# Checkpoint v2 Decision-Boundary Protocol

## Scope

This document freezes the P2-D2 checkpoint boundary used by MiniToolBench-R
multi-step tasks. It extends the existing Checkpoint v2 implementation; it does not
replace the B2/B3/B4 protocol or change CONTINUE semantics.

The protocol captures a non-terminal decision point after a local task tool has
finished and its Observation or Agent-visible Error has been appended to the current
`ActionStep`, but before the next model request and before deterministic verification.

## Authoritative lifecycle boundary

```text
Assistant Tool Call
  -> local tool execution settles
  -> Observation/Error appended to ActionStep
  -> ActionStep appended to Agent Memory
  -> DecisionCheckpointCapture.capture()
  -> CheckpointManager.snapshot_v2()
  -> stop before the next model request
```

Capture is accepted only when all of the following are true:

- the supplied event is the latest Agent Memory step and is an `ActionStep`;
- every announced tool call is settled and call IDs/arguments match execution;
- the step contains an Observation or Agent-visible Error;
- the step is non-terminal and the verifier has not run;
- no tool call remains active;
- step, model-call, tool-call, token, and latency budgets allow continuation;
- `RecordingModel.attempted_calls` has not advanced beyond the completed step;
- the canonical message sequence ends in a tool response;
- capture does not issue a provider request or execute another tool.

The capture granularity is one completed ActionStep observation batch. A step with
multiple tool calls is eligible only after the complete batch settles.

## P2-D1 multi-step integration

P2-D1 tasks use the normal smolagents lifecycle and the same local tool adapters as
the historical gates. Later tool arguments are constructed from actual earlier tool
results, not from evaluator reference outputs. The model-visible surface consists of:

- the task prompt;
- public schemas for tools allowed in the task's domain;
- prior Assistant Tool Calls and their actual Observations or Errors.

`reference_steps`, `expected`, split-control metadata, fault plans, verifier labels,
and future branch outcomes are evaluator-only and are never inserted into Agent
Memory or the canonical next-model input.

P2-D2 local acceptance uses new Train templates only. Validation and Test tasks are
not used for protocol debugging, threshold selection, or fixture construction.

## Frozen snapshot contents

### Identity

New snapshots bind these values: experiment, run and task-run IDs; template, task and
instance IDs; split; checkpoint ID; catalog and split-manifest hashes; model ID and
version; worker ID; prompt/tool/verifier versions; controller and execution mode;
generation configuration; task seed; completed step index; and creation time.

The task seed is also part of `identity_config_hash`. Historical schema `2.0.0`
snapshots that predate the explicit identity seed remain readable and verifiable;
new snapshots always write it. Runtime seed matching remains mandatory for both
historical and new snapshots.

### Agent state

The Agent artifact contains the complete system prompt, task prompt, TaskStep and
ActionSteps, Assistant Tool Calls, tool Observations/Errors, current and remaining
step counters, public tool registry, model generation configuration, and exact
canonical next-model input.

At the capture boundary, `RuntimeState.agent_memory` is refreshed from the same
authoritative `AgentStateSnapshot.memory.steps`. This prevents Agent and Runtime
views from diverging while retaining separate serialized authorities.

### Environment state

The Environment artifact contains the workspace file manifest and SHA-256 values,
SQLite backup and normalized logical dump, Mock API tables, completed operations,
pending-operation state, latest completed operation, write commit information, and
idempotency records. Restore installs these artifacts into an independent task
workspace without replaying historical tools.

### Runtime state

The Runtime artifact contains Tool History, the Agent Memory projection, tool state,
tool/fault adapter status and state, budgets, model-call counter, token usage, and
observed model/tool latency. A genuinely unobservable quantity is serialized as
`null`; zero is used only for a measured zero, such as tool-token usage when local
tools do not consume model tokens.

## Hash contract

All hashes use the repository's canonical JSON normalization. Every new checkpoint
contains exactly these six state hashes:

1. `agent_state_hash`
2. `next_model_input_hash`
3. `environment_state_hash`
4. `runtime_state_hash`
5. `identity_config_hash`
6. `combined_state_hash`

`combined_state_hash` binds the schema version and the preceding five hashes.
Artifact files additionally carry size and SHA-256 registrations in the manifest.

## Independent restore and no-replay rule

`CheckpointManager.restore_v2()` verifies manifest shape, registered artifact
integrity, task identity, seed, split, Agent state, Runtime state, Environment state,
identity configuration, and combined hash before installing a restored workspace.

After restore:

- the Agent and model client are rebuilt as new objects;
- local tool adapter cursors are restored from the checkpoint;
- completed source-call signatures are installed in the replay guard;
- Agent Memory is restored with `AgentStateAdapter.restore()`;
- the first request contract is built only from frozen checkpoint fields;
- its canonical input hash must equal `next_model_input_hash` before provider access.

No restore path may reconstruct state by re-executing completed tools. A matching
completed call raises `CompletedToolReplayError` before tool execution, logging, or
budget mutation. This applies to read calls and to state-changing API calls; the
Environment snapshot and idempotency table are the authority for completed writes.

The source checkpoint directory is immutable during verification and restore.

## Compatibility and acceptance gates

The local P2-D2 gate covers one new Train multi-step task from each domain. Each task
runs through two dependent local tool calls on the actual smolagents execution path,
then verifies capture boundary, complete state, independent restore, six matching
hashes, exact next input, no replay, checkpoint immutability, split rejection, hidden
information isolation, and historical v2 shape compatibility. Existing tests retain
v1 loading and multiple-call batch coverage.

The real-Qwen compatibility gate is separate. It requires explicit user approval,
at least five new v2 Train tasks covering all four domains, and 5/5 exact restore and
next-input matches. Historical B2/B3/B4 smoke artifacts do not satisfy this gate and
no P2-D2 smoke output is formal training data.
