# B2 Day 3 Execution Design

## Components

- `CheckpointableToolCallingAgent`: pinned continuation loop over smolagents' real `_step_stream`.
- `DecisionCheckpointCapture`: validates the first eligible yielded ActionStep and synchronously
  calls the existing `CheckpointManager.snapshot_v2()`.
- `RecordingModel`: records the actual generate boundary and rejects Message, Tool Schema, model,
  generation or restored Model Call counter mismatch before delegation.
- Existing `AgentStateAdapter` and Checkpoint v2 remain the only state and artifact formats.

## Capture flow

The source is advanced only to the first non-terminal ActionStep yield. The capture object proves
Memory identity, Observation/error presence, Assistant/executed call-ID agreement, settled tool
batch, canonical Tool Response tail, unused Model/Token/latency/step budget, no verifier call and
no attempted next request. It then derives `CaptureInvariants` from those checks and snapshots
synchronously. Rejections are recorded by reason and create no Checkpoint.

## Resume flow

Checkpoint v2 verifies all artifacts before replacing the isolated restore Workspace. A fresh
model/tool/Agent graph is constructed, RuntimeState is reused from the verified result,
`AgentStateAdapter.restore()` installs Memory and cursor state, and the RecordingModel restores
the prior Model Call count. `resume()` begins at the next step without adding a TaskStep. The first
actual request is checked before the underlying model is called.

## Independent input evidence

Path A advances the original generator after capture and intercepts its next actual generate call.
Path B restores the immutable Checkpoint into a separate Workspace and intercepts the fresh
Agent's first actual generate call. Neither path reads the other's request object. Canonical bytes
and both hashes are compared with the Checkpoint `next_model_input_hash`. Negative probes alter
Observation content, Tool Call ID text and generation configuration; all are rejected before model
execution.

## Side-effect and lineage policy

The local write probe uses an idempotency key and a file counter. Source execution changes zero to
one; capture, close and restore leave it at one. The restored first model response is a final
answer, so no task tool is called. Runtime Tool History retains exactly one completed operation.
Raw Python results, Tool History, ActionStep Observation, canonical Tool Response and final
Messages are distinct evidence layers. No internal fault code or verifier field is exposed as an
Agent-visible feature.

## Evidence classification

All Day 3 artifacts declare `formal_experiment=false`, `real_qwen_executed=false`,
`model_backend=local_scripted_model`, and `research_dataset_eligible=false`. Schema-constrained
identity fields inside synthetic Checkpoint fixtures are not evidence of Qwen execution.
