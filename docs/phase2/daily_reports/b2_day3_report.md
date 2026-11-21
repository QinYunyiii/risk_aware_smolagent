# B2 Day 3 Report

## Objectives

- Integrate the existing Agent snapshot and Checkpoint v2 with the actual local smolagents stream
  lifecycle.
- Capture synchronously after a complete ActionStep Observation batch and before the next model
  request.
- Resume a freshly rebuilt Agent without adding a TaskStep, resetting counters or replaying a
  completed tool call.
- Compare independent original and restored actual model-request boundaries byte-for-byte.

## A. Lifecycle Audit

- Installed framework: `smolagents==1.26.0`.
- Source: `.venv/lib/python3.12/site-packages/smolagents/agents.py`.
- `run()` is at line 436, `_run_stream()` at 540, `_finalize_step()` at 620,
  `write_memory_to_messages()` at 758, `_step_stream()` at 1276 and `process_tool_calls()` at
  1361 in the installed version.
- Framework order is finalize/callback -> Memory append -> ActionStep yield -> step increment.
- At capture, step 1 is completed, `step_number` remains 1, next step is 2 and remaining steps are
  9 of 10.
- `generator.close()` at the ActionStep yield caused zero extra model calls, tool calls or file
  writes.
- `run(reset=False)` is not a resume API because it still adds TaskStep and `_run_stream` resets
  the cursor. The local version-pinned subclass supplies the minimal continuation loop.

Full evidence: `docs/phase2/b2_day3_lifecycle_audit.md`.

## B. Engineering Implementation

### New files

- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/collection/decision_checkpoint.py`
- `tests/agent/test_checkpointable_agent.py`
- `docs/phase2/b2_day3_lifecycle_audit.md`
- `docs/phase2/b2_day3_execution_design.md`
- local evidence under `runs/phase2_b2_day3/20261121_local_lifecycle/`

### Modified files

- `src/risk_agent/agent/__init__.py`
- `src/risk_agent/collection/__init__.py`
- `src/risk_agent/checkpoint/v2_manager.py`

The v2 manager now accepts the controlled ToolCallingAgent subclass and preserves an announced
Tool Call ID when local execution produces an Agent-visible error. Protocol, JSON Schema, labels,
limits and splits are unchanged.

`DecisionCheckpointCapture` measures real invariants and records rejection reasons; it does not
write hard-coded evidence without checking the live Agent. `RecordingModel` distinguishes
attempted, rejected, executed and successful requests and rejects mismatches before delegation.

## C. Exact Input Verification

| Field | Original control | Checkpoint | Restored path |
|---|---|---|---|
| Input SHA-256 | `9d20ac3c...2bdfef` | `9d20ac3c...2bdfef` | `9d20ac3c...2bdfef` |
| Canonical bytes | 4,303 | 4,303 | 4,303 |
| Exact bytes match | PASS | PASS | PASS |
| Model calls at capture | 1 | 1 | 1 restored |
| Model calls after request | request intercepted | unchanged | 2 |
| Tool calls | 1 | 1 | 1 |

Path A advanced the source generator to its actual next generate boundary and stopped before the
underlying scripted response. Path B restored a separate Checkpoint Workspace and executed its
actual first generate boundary. The paths created their own request records. Observation, Tool
Call ID and Generation Config mutation probes were each rejected before underlying model execution.

## D. Side-effect Audit

- Completed operations at capture: 1.
- Restore operations: 0 task-tool calls.
- Unexpected replay count: 0.
- Idempotent file counter: `0 -> 1` on source tool execution; remained `1` after capture, source
  close, restore and restored model request.
- Tool History entries after restore: 1.
- SQLite changes caused by restore: 0 beyond installing the verified backup.
- Mock API changes caused by restore: 0 beyond installing the verified state.
- Read-only two-call batches preserve deterministic call-ID/Observation order.
- Agent-visible local tool errors preserve call ID and source close does not retry them.
- Failed capture probes create no Checkpoint and cause no extra side effect.

## E. Budget Audit

- Source completed step: 1; restored continuation step: 2; final cursor after step: 3.
- `remaining_steps` at capture: 9.
- Model calls: 1 at capture, restored as 1, then 2 after the allowed request.
- Tool calls: 1 before and after restore.
- Aggregate Tokens: 6 before and after restore.
- Execution latency: 0.01 seconds before and after restore.
- Null/zero behavior remains covered by Day 1/2 and RuntimeState regressions.
- Exhausted Token and Step/model/latency boundaries are rejected by Day 3 capture plus Day 2
  hardening tests.

Human pause duration versus total wall-clock budget is not fully specified by the frozen protocol.
This implementation preserves accumulated execution latency and does not invent a pause rule.

## F. Tests

```text
.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_checkpointable_agent.py
14 passed in 0.82s

.venv/bin/python -m pytest -q --disable-warnings tests/agent/
22 passed in 0.82s

.venv/bin/python -m pytest -q --disable-warnings tests/test_runtime_state.py
12 passed in 0.01s

.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint.py
7 passed in 0.48s

.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint_v2.py
33 passed in 1.34s

.venv/bin/python -m pytest -q --disable-warnings
151 passed in 9.65s

.venv/bin/python -m compileall -q src tests
no output; exit 0

git diff --check
no output; exit 0
```

New Day 3 tests: 14 passed, 0 failed, 0 skipped. Full historical regression: 151 passed, 0
failed, 0 skipped.

## G. Git Status

- Before HEAD: `f51a8d63297ef243757f2f97b3f6893b6afd6b0c`.
- Intended commit: `feat: implement checkpointable agent lifecycle and exact resume`.
- Push: prohibited for this task; no Push performed.

The final commit SHA is reported after this report and all evidence are committed together.

## Remote Environment Work

None. Qwen API requests: 0; SSH/API operations: 0; GPUs used: 0; vLLM operations: 0; Docker
experiments: 0. All evidence declares local scripted, non-formal and dataset-ineligible status.

## H. Final Gate

## Status: PASS

- [x] Installed smolagents lifecycle has source and live-probe evidence.
- [x] Capture is after Observation append and before the next inference.
- [x] Capture and source close add no model/tool side effect.
- [x] Agent Memory and execution cursor restore without TaskStep duplication.
- [x] Runtime budgets and Model Call counter remain continuous.
- [x] Independent original/restored actual request bytes and hashes match.
- [x] Negative mismatches block the underlying request.
- [x] Completed Tool Calls and writes are not replayed.
- [x] Original Checkpoint remains immutable.
- [x] v1/v2 and full repository regressions pass.
- [x] Remote-resource use is zero.

This PASS is the local Day 3 engineering Gate requested here. It does not claim the acceptance
protocol's five real-Qwen Checkpoints, B3 CONTINUE labels, Day 4 branch isolation or formal data
collection. Stop after Day 3 and wait for user review.

## Git Commit

Pending the dedicated local commit created after final diff and sensitive-data review.
