# B2 Day 4 Post-Audit Hardening Design

## Scope

This local-only hardening fixes two implementation gaps without changing Protocol v2, Checkpoint
Schema v2.0.0, labels, split rules or frozen limits. It uses smolagents 1.26.0 and a deterministic
ScriptedModel. Qwen, GPU, SSH, vLLM and Docker are outside this run.

## Request-boundary state machine

`RecordingModel` now separates two responsibilities:

1. `awaiting_first_restored_request` strictly validates the Checkpoint Messages/hash, restored
   Model Call counter, live Runtime budget, next Step, Model ID, Generation Config and ordered
   public Tool Schemas.
2. `first_restored_request_verified` stops comparing later Messages with the frozen first input,
   but retains Model ID, Generation Config, Tool Schema, live Model/Token/latency/Step guards and
   invalid Tool Call budget-state checks for every request.

The transition to verified occurs only immediately before delegation. A local mismatch or valid
audit interception leaves the first contract pending. A provider exception occurs after
delegation, so it consumes a Model Call and leaves a failed request record. The contract cannot be
reinstalled, the prior counter cannot be restored twice, and callers cannot clear the private
contract through the public read-only property.

`CheckpointableToolCallingAgent.resume()` fails closed unless its RecordingModel has both an
unconsumed first-request contract and a live RuntimeState budget authority. Normal new-task
execution remains compatible: no resume contract is required, while an explicitly configured
Model Call limit is still enforced.

## Authoritative live budgets

Model Call usage remains `prior_model_calls + executed_calls`; RecordingModel is its authority.
The RuntimeState bound to the restored Agent is the authority for cumulative Tokens, Tool Calls
and execution latency. Successful model responses add observed Prompt and Completion Tokens plus
measured model latency. Provider failures add measured latency and preserve missing Token status;
they do not invent zero Token usage. A subsequent request fails closed with
`token_usage_unavailable` unless the Model Call limit already supplies the stricter rejection.

Tool execution continues to update the same RuntimeState through the tool adapter. Tool Call
usage equal to the limit does not prevent a final-answer model request; usage above the limit is
an invalid state and is rejected. The live Agent supplies `(step_number, max_steps)` at every
model boundary to prevent off-by-one continuation after exhaustion.

## Multi-step continuation

The end-to-end fixture performs one pre-Checkpoint model/tool step, captures Checkpoint v2, closes
the source generator, restores into a fresh environment, then executes three actual model
requests through `CheckpointableToolCallingAgent.resume()`:

- restored request 1 executes Tool Call 2 and consumes the one-shot exact-input contract;
- restored request 2 has different genuine Memory Messages and executes Tool Call 3;
- restored request 3 has different Messages and returns FinalAnswer.

The test checks four sequential ActionSteps, one TaskStep, three Tool History entries, continuous
Model Calls `1 -> 2 -> 3 -> 4`, observed Tokens `6 -> 12 -> 18 -> 28`, no replay of Tool Call 1,
and an unchanged Checkpoint inventory. Separate lifecycle cases cover a provider exception on the
second resumed request, Token exhaustion before the second resumed request, an Agent-visible tool
error followed by successful continuation, and missing resume contract rejection.

## TaskSpec ownership

`TaskEnvironment` deep-copies its incoming frozen TaskSpec because the dataclass boundary does not
recursively freeze nested mappings. Each source/A/B environment therefore owns independent
inventory, order, expected and ToolStep argument containers. Tests mutate A then B and verify the
opposite branch and source remain unchanged while task/template/instance IDs, seed and split stay
equal. FileFixture currently contains strings only, so it has no nested mutable state to isolate.

## Evidence

`scripts/audit_b2_day4_post_hardening.py` runs real local scenarios and the required test commands,
then writes append-only raw summaries under `runs/phase2_b2_day4_post_audit/<run_id>/` and matching
sanitized, commit-safe summaries under `docs/phase2/evidence/day4_post_audit/<run_id>/`. Evidence
contains hashes and counters but no raw model Messages, database copy, secret or remote credential.
Every document is marked non-formal, local-scripted, GPU-free and research-dataset-ineligible.
