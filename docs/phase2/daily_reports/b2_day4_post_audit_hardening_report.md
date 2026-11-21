# B2 Day 4 Post-Audit Hardening Report

## Objectives

- Decouple the one-shot exact restored-input contract from persistent request budgets.
- Prove at least three consecutive post-Restore model requests through the real smolagents loop.
- Continuously update observable Token/model-latency usage without losing Provider failures.
- Eliminate nested TaskSpec sharing across independent TaskEnvironment branches.
- Produce commit-safe, sanitized and independently checkable local evidence.

## Local Environment Work

All work ran locally in WSL with `.venv/bin/python` 3.12.3 and smolagents 1.26.0. Baseline HEAD
and `origin/main` were both `52e97e7b37b3d88b65acbd8cffcad644dff232c1`; the initial worktree was clean.
No frozen Protocol, Checkpoint Schema, label, split, task template or budget limit changed.

## Remote Environment Work

None. Qwen API requests: 0; GPUs used: 0; SSH sessions: 0; vLLM operations: 0; Docker
operations: 0.

## Work Completed

### A. Git Status

- Branch: `master` tracking `origin/main`.
- Baseline Commit: `52e97e7b37b3d88b65acbd8cffcad644dff232c1`.
- New Commit: created after this report and evidence are staged; authoritative SHA is the Git
  commit containing this report and is included in the final delivery response.
- Initial worktree: clean.
- Push: not performed.

The original Day 4 report retains its point-in-time uncommitted statement and now has a separate
Post-Commit Note recording that Day 4 was later committed and pushed as the baseline above.

### B. Modification Summary

Root cause: `ExpectedModelRequest` represented the first input after Restore, but the same frozen
Messages and usage counter were compared on every later request. Clearing it would allow later
requests but also remove budget enforcement. Separately, TaskEnvironment retained the caller's
TaskSpec object even though nested mappings were mutable.

The fix introduces a one-shot resume-contract state machine plus an independent persistent guard.
The live RuntimeState is authoritative for Token, Tool Call and latency usage; RecordingModel is
authoritative for delegated Model Calls. `resume()` requires both the pending contract and bound
RuntimeState. TaskEnvironment deep-copies TaskSpec on ownership entry.

Existing construction remains compatible because the new RecordingModel `runtime_state` and
`max_model_calls` parameters are optional for normal non-resume use. Checkpoint resume now fails
closed if callers omit the live budget binding or first contract. Protocol and serialized schema
are unchanged.

### C. RecordingModel Acceptance

- First Request Validation: exact ordered Messages/hash, Model ID, ordered Tool Schemas,
  Generation Config, restored counters and next Step pass on the first restored request.
- Expected Contract Lifecycle: `normal_execution -> awaiting_first_restored_request ->
  first_restored_request_verified`; local rejection/interception does not consume it, while a
  delegated Provider failure does.
- Persistent Budget Guard: every later request checks Model Call, Token, latency and Step state;
  Tool Call usage above its limit is rejected while equality still permits FinalAnswer generation.
- Provider Failure Accounting: at prior usage 9, one failing delegated request produces 10/10;
  the following attempt is rejected as `model_call_budget` with one Provider call total.
- Second/Third Generate: changed genuine Memory Messages pass, but Model ID, Tool Schema and
  Generation Config mutation after the first request are rejected before delegation.
- Missing Token Usage: recorded as unavailable, never converted to zero; the next request fails
  closed with `token_usage_unavailable`.
- Counter/contract reset prevention: repeated `restore_model_calls_used()` and `expect()` calls are
  rejected.

### D. Multi-Round CONTINUE Test

| Request | Step | First restored request | Input validation | Model Calls | Tokens | Result |
|---:|---:|---:|---|---:|---:|---|
| #1 | 1 | no | captured Checkpoint input | 1 | 6 | Tool Observation captured |
| #2 | 2 | yes | exact Checkpoint contract | 2 | 12 | Tool Call succeeded |
| #3 | 3 | no | persistent identity/budget guard | 3 | 18 | Tool Call succeeded |
| #4 | 4 | no | persistent identity/budget guard | 4 | 28 | FinalAnswer `done` |

- ActionSteps: 4 with step numbers `[1, 2, 3, 4]`.
- TaskSteps: 1; no duplicate TaskStep.
- Tool History: 3 completed counter operations.
- Counter: `0 -> 1` before capture, then `2 -> 3` after Restore.
- Unexpected replay of Tool Call #1: 0.
- FinalAnswer generated: yes (`done`).
- First restored input hash and Checkpoint input hash:
  `9071ccf9c7185546bfa712018a77cbf66f5cc224cb4b1e1520f480b0fc550749`.
- Checkpoint inventory: unchanged.

Failure lifecycle cases also pass: second resumed Provider failure is counted once without retry;
the first resumed response can exhaust Token budget and block the second; Agent-visible tool error
preserves its Assistant call ID and a later request can still produce FinalAnswer; missing resume
contract prevents any model attempt.

### E. TaskSpec Isolation

- Source, Branch A and Branch B TaskSpec instances are all distinct.
- Inventory row, Order row, Expected mapping and ToolStep.arguments containers are distinct.
- A mutations do not affect B or Source; B mutations do not affect A or Source.
- Task ID, Template ID, Instance ID, seed and Train split remain equal.
- FileFixture has only immutable string fields, so nested mutable fixture state is not applicable.
- Existing TaskSpec equality/serialization and all historical task tests remain passing.

### F. Regression Tests

| Command | Passed | Failed | Skipped | Pytest duration |
|---|---:|---:|---:|---:|
| `tests/agent/test_checkpointable_agent.py` | 31 | 0 | 0 | 1.33s |
| `tests/agent/test_multistep_resume.py` | 5 | 0 | 0 | 0.73s |
| `tests/test_checkpoint_v2_isolation.py` | 7 | 0 | 0 | 0.94s |
| `tests/agent/` | 44 | 0 | 0 | 1.67s |
| `tests/test_runtime_state.py` | 12 | 0 | 0 | 0.01s |
| `tests/test_checkpoint.py` | 7 | 0 | 0 | 0.46s |
| `tests/test_checkpoint_v2.py` | 33 | 0 | 0 | 1.38s |
| `tests/test_branching.py` | 2 | 0 | 0 | 0.65s |
| full repository | 180 | 0 | 0 | 11.31s |

`compileall -q src tests` and `git diff --check` both exited 0 with no output. The historical 159
tests remain; 21 tests were added and none deleted or skipped.

### G. Evidence Files

- Accepted Run ID: `20261121T224116+0800`.
- Raw local summary: `runs/phase2_b2_day4_post_audit/20261121T224116+0800/`.
- Commit-safe evidence: `docs/phase2/evidence/day4_post_audit/20261121T224116+0800/`.
- Files: `test_summary.json`, `multistep_resume_audit.json`, `model_budget_audit.json`,
  `taskspec_isolation_audit.json`, `checkpoint_integrity_audit.json`, `evidence_manifest.json`.
- Evidence Manifest hashes all five payload documents.
- Contains secrets/raw Messages/database backup: no.
- Formal research data or real Qwen evidence: no.

The earlier runs `20261121T223550+0800`, `20261121T223840+0800` and
`20261121T224020+0800` are preserved as pre-final engineering evidence because later guard tests,
command-path redaction and monotonic duration measurement were added. They are not the accepted
final evidence set.

### H. Remote Resource Usage

- Qwen API requests: 0.
- GPU devices used: 0.
- SSH sessions: 0.
- vLLM start/stop/restart operations: 0.
- Docker operations: 0.

### I. Gate Conclusion

## Status: PASS

All P1 multi-round continuation, persistent budget, failure-accounting and final-answer Gates pass.
TaskSpec deep isolation, Checkpoint immutability, sanitized evidence and all compatibility
regressions pass. This is a local scripted engineering Gate only.

### J. Next Stage

The implementation is locally ready for a separately approved B2 Day 5 real-Qwen validation.
Day 5 must still establish at least five real Qwen complete Checkpoints, exact first input and
controlled continuation with remote identity/TLS/GPU lineage. B3 CONTINUE collection has not
started, B4 smoke has not started, and formal data collection remains prohibited until sequential
B2/B3/B4 Gates pass.

## Files Changed

- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/benchmark/environment.py`
- `tests/agent/test_checkpointable_agent.py`
- `tests/agent/test_multistep_resume.py`
- `tests/test_checkpoint_v2_isolation.py`
- `scripts/audit_b2_day4_post_hardening.py`
- Day 4 Post-Commit Note, hardening design/report and versioned sanitized evidence.

## Artifacts Generated

The accepted ignored local run and commit-safe evidence set listed in section G. No historical run
or evidence was overwritten or deleted.

## Commands and Tests

Exact commands, exit codes, wall durations and summaries are stored in the accepted
`test_summary.json`. All commands required by the task were executed in the specified order.

## Test Results

180 passed, 0 failed, 0 skipped. Static compilation and Git diff validation passed.

## Acceptance Checklist

- [x] One-shot exact first restored request validation.
- [x] Persistent live budget and identity guards.
- [x] Provider failures consume Model Call budget.
- [x] Three consecutive post-Restore model requests and FinalAnswer.
- [x] No TaskStep or historical Tool Call replay.
- [x] Observable Token/latency updates use live RuntimeState.
- [x] Missing Token usage is not imputed as zero.
- [x] TaskSpec nested state is bidirectionally isolated.
- [x] Original Checkpoint remains unchanged.
- [x] v1/v2 and Day 1--4 regressions pass.
- [x] Sanitized evidence is commit-safe and non-formal.
- [x] Remote-resource use is zero.

## Known Issues

This run does not validate real Qwen/vLLM behavior. A Provider response without Token Usage now
fails closed before a later request; it is not silently imputed, but real service missingness still
needs explicit Day 5 observation. The audit script imports deterministic test fixtures by design
and is an engineering evidence generator, not a production execution entry point.

## Remaining Work

Only the separately approved Day 5 real-Qwen acceptance work described in section J. No Day 5,
B3, B4 or formal collection was executed here.

## Git Commit

Intended single local commit: `fix: harden multi-step resume budget and task isolation`. The final
SHA is reported after commit creation. Push remains prohibited.
