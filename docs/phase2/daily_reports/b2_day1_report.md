# B2 Day 1 Report

## Objectives

- Freeze Protocol v2 as `risk-continue-v2.0.0` with traceable rc1 provenance.
- Implement a versioned `AgentStateAdapter` against the installed `smolagents==1.26.0`.
- Preserve and restore canonical Messages and complete `AgentMemory` steps.
- Prove Message and Memory round trips locally without model or tool execution.

## Local Environment Work

- Audited the installed `ToolCallingAgent`, `AgentMemory`, `TaskStep`, `ActionStep`,
  `PlanningStep`, `ChatMessage` and `write_memory_to_messages()` implementations.
- Added a JSON-safe Agent snapshot envelope with a canonical SHA-256 state hash.
- Added explicit serializers and deserializers for Messages, model tool calls, framework tool
  calls, timings, token usage, task/action/planning steps, errors and PIL images.
- Added capture/restore of task, system prompt, prompt templates, Agent state, step counters,
  remaining step budget, model generation identity and ordered Tool Registry.
- Reconstructed the next model input only through the authoritative
  `agent.write_memory_to_messages()` path and compared its exact canonical hash.
- Kept provider response objects, HTTP clients, API keys, TLS material, sockets and Python
  process objects outside the snapshot.

## Remote Environment Work

None. No SSH/API request, GPU inspection, model request, service change or remote resource use
was performed. Day 1 is entirely local.

## Work Completed

- Phase 0 formal freeze was completed in independent commit `19f54ad`.
- Message round trip preserves role, content, Assistant Tool Call name/arguments/ID and token
  usage, including the difference between `0` and `null`.
- Memory round trip covers `TaskStep`, `ActionStep` and `PlanningStep`.
- Action round trip preserves Tool Call, Tool Observation, rendered error, output and timing.
- Agent restore verifies framework, Agent class, model generation config, Tool Registry and
  prompt-template identity before installing state.
- Snapshot corruption and incompatible rebuilt models are rejected.

## Files Changed

- `src/risk_agent/agent/agent_state_adapter.py` (new)
- `src/risk_agent/agent/__init__.py`
- `tests/agent/test_agent_state_adapter.py` (new)
- `docs/phase2/daily_reports/b2_day1_report.md` (new)

Phase 0 freeze files are listed separately in `protocol_freeze_report.md` and commit `19f54ad`.

## Artifacts Generated

- `AgentStateSnapshot` schema version `2.0.0`.
- Canonical `agent_state_hash` and `next_model_input_hash` implementations.
- Eight focused Day 1 tests.
- Protocol freeze record and report from Phase 0.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/agent/test_agent_state_adapter.py
.venv/bin/python -m pytest -q
git diff --check
```

The adapter inspection also used local Python reflection against the pinned installed package;
it did not modify site-packages.

## Test Results

- Day 1 focused suite: `8 passed in 0.25s`.
- Full repository regression suite: `92 passed in 8.69s`.
- Pre-Day-1 baseline was 84 passing tests; all eight new tests pass without regressions.
- No remote or real-Qwen test was run, as required by the Day 1 boundary.

## Acceptance Checklist

- [x] rc1-to-formal differences and pre-edit SHA-256 values are recorded.
- [x] Message round trip passes.
- [x] Assistant Tool Call ID, name and arguments are preserved.
- [x] Tool Observation is preserved.
- [x] Task, Action and Planning Memory steps round trip.
- [x] Complete model-visible Agent state can be captured and restored.
- [x] Exact canonical next-model-input hash matches after restore.
- [x] Null and zero remain distinct.
- [x] Live clients and credentials are not serialized.
- [x] Historical regression suite passes.

## Known Issues

- This is the Day 1 adapter layer only. Checkpoint artifact storage, v1/v2 manifest dispatch,
  environment/runtime component hashes and corrupt-artifact restore rejection belong to Day 2.
- Capture timing, lifecycle spy assertions and no-repeat tool execution belong to Day 3.
- Workspace/SQLite branch isolation and the complete local B2 Gate belong to Day 4.
- A provider `ChatMessage.raw` transport object is intentionally excluded; the model-visible
  role/content/tool calls/token usage are preserved and verified.
- Arbitrary unsupported Python objects cause capture to fail explicitly instead of being
  converted to lossy strings.

## Remaining Work

Stop after Day 1 and wait for user review. After approval, Day 2 may extend the existing
Checkpoint Manager with schema v2, component/combined hashes, artifact integrity validation and
v1 compatibility. Remote Qwen work remains unauthorized until Days 1--4 pass and the user grants
separate resource approval.

## Status: PASS

Day 1 acceptance is complete. This status does not claim that B2 as a whole or the local B2 Gate
is complete.

## Git Commit

The Day 1 code, tests and this report are committed together in a dedicated commit. Resolve its
non-self-referential SHA with:

```bash
git log -1 --oneline -- docs/phase2/daily_reports/b2_day1_report.md
```
