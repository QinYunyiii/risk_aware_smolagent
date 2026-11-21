# B2 Day 2 Checkpoint v2 Hardening Report

## Objectives

- Re-accept the Day 2 Checkpoint v2 implementation after addressing three identified data
  integrity and state-consistency issues.
- Make RuntimeState serialization fail closed for values that cannot be represented losslessly in
  JSON.
- Require real remaining budget for the declared next `model_generation` operation.
- Align restore behavior with the frozen optional `latest_completed_operation_ref` field.
- Preserve Checkpoint v1 compatibility and all existing Checkpoint v2 integrity checks.

## Local Environment Work

All implementation and validation work was performed locally in WSL. The baseline was the clean
`master` worktree at Day 2 commit `bc64130cb35b509edd530a18a1bd2e351fddff62`.

The frozen Protocol v2 documents, JSON Schema, acceptance protocol and collection configuration
were reviewed but not modified. The current smolagents execution loop was also checked to confirm
that exhausting Tool Call budget does not by itself prohibit the next model request: that request
may still return the final answer without issuing another Tool Call.

## Remote Environment Work

None. This task made zero Qwen API requests, used zero GPUs, made no SSH or remote API connection,
did not start or restart vLLM, and did not start Docker experiments.

## Work Completed

### 1. Strict Runtime JSON serialization

**Observed behavior:** `RuntimeState` used `json.dumps(..., default=str)`, so objects outside the
JSON data model could silently become lossy strings. Deserialization also coerced some scalar
types with `str()`, `int()` and `float()`.

**Root cause:** the Runtime serialization boundary prioritized permissive conversion over exact
state preservation and did not recursively validate finite numbers or string dictionary keys.

**Fix:** Runtime values are now recursively restricted to `None`, booleans, integers, finite
floats, strings, lists and dictionaries with string keys. Serialization uses `allow_nan=False`
without a fallback encoder. Budget, Runtime and Tool History deserialization validate types
instead of silently coercing them. `ExecutionMonitor` no longer converts non-string keys to
strings before Runtime capture. Explicit `null`, numeric zero, `false` and empty strings remain
distinct.

### 2. Remaining-budget validation

**Observed behavior:** a Checkpoint could declare
`after_tool_observation_before_model_generation` while Model Call, cumulative Token, latency or
Agent step budget was already exhausted. Verification did not independently reject a
self-consistently rewritten exhausted Checkpoint.

**Root cause:** metadata only rejected Model Call usage strictly greater than its limit, and the
snapshot/verification paths lacked one shared semantic validation of all budgets required by the
declared next operation.

**Fix:** snapshot and verification now apply the same continuation-budget validation. They require
`model_calls_used < max_model_calls`, authoritative aggregate `RuntimeState.budget.tokens_used <
max_tokens`, elapsed latency below its limit, and a consistent positive remaining Agent step.
When both split Token counters are observed, their sum must equal the authoritative aggregate;
missing Prompt/Completion counters remain `null` and are never treated as zero. Tool Call usage
equal to its limit is permitted because the next model response may be a final answer without a
Tool Call; usage above the limit is invalid. Snapshot rejection occurs before staging, and restore
verification occurs before target Workspace or database mutation.

### 3. Optional latest completed operation

**Observed behavior:** the frozen Schema declared `latest_completed_operation_ref` optional, but
verification indexed it as a required key and could raise `KeyError`.

**Root cause:** the writer/reader behavior was stricter than the optional Schema contract.

**Fix:** snapshots omit both the reference and artifact when there is no latest completed
operation. Verification uses safe optional lookup and feeds an explicit `None` into the canonical
Environment hash document. When the reference is present, closed artifact-set validation,
checksum/size verification and content hashing remain mandatory.

## Files Changed

- `src/risk_agent/monitoring/runtime_state.py`
- `src/risk_agent/monitoring/execution_monitor.py`
- `src/risk_agent/checkpoint/metadata.py`
- `src/risk_agent/checkpoint/v2_manager.py`
- `tests/test_runtime_state.py` (new)
- `tests/test_checkpoint_v2.py`
- `docs/phase2/daily_reports/b2_day2_hardening_report.md` (new)

No frozen Protocol, JSON Schema, risk label, budget limit or dataset split file was changed.

## Tests Added and Purpose

- Strict Runtime round trips for normal nested JSON and Tool History.
- Preservation of `None`, zero, `False` and empty-string distinctions.
- Rejection of `object()`, tuple, set, complex values, non-string keys and non-finite floats.
- Rejection of silent scalar coercion during deserialization.
- Snapshot boundaries for Model Call, aggregate Token, latency, Agent step and Tool Call budgets.
- Preservation of missing Prompt Token counters versus observed zero.
- Verification/restore rejection of internally checksum-consistent but semantically exhausted
  Model Call, Token and latency budgets before Workspace mutation.
- Snapshot/verify/restore with the optional latest-operation reference absent.
- Restore rejection when a present latest-operation artifact is missing or tampered.

All Checkpoint tests use temporary local Workspace and SQLite fixtures. They exercise actual local
serialization, artifact hashing, verification and restoration; no model API Mock substitutes for
those operations.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q --disable-warnings tests/test_runtime_state.py
.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint_v2.py
.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint.py tests/test_checkpoint_v2.py
.venv/bin/python -m pytest -q --disable-warnings tests/agent/
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests
git diff --check
```

## Test Results

- RuntimeState focused tests: `12 passed in 0.01s`.
- Checkpoint v2 focused tests: `33 passed in 1.32s`.
- Combined v1/v2 Checkpoint compatibility tests: `40 passed in 1.56s`.
- Agent State regression tests: `8 passed in 0.21s`.
- Full repository regression: `137 passed in 9.15s`.
- Python bytecode compilation: passed with no output.
- Diff whitespace validation: passed with no output.
- No Ruff, Mypy or other project static-check configuration was present, so no additional
  configured static checker was available to run.

## Compatibility and Integrity Review

- Checkpoint v1 read/snapshot/restore compatibility remains covered by the combined 40-test run.
- Checkpoint v2 Schema validation, all six state hashes, Artifact SHA-256/size checks, SQLite
  Backup and Logical Dump checks, Workspace recovery, corruption rejection, symlink/path safety
  and atomic rollback remain enabled.
- The optional latest-operation representation is now Schema-consistent without creating a fake
  reference or artifact.
- Historical valid Checkpoints containing strict JSON Runtime data remain readable. A historical
  Runtime document that depended on undocumented scalar coercion or non-finite/non-JSON values is
  now deliberately rejected to protect state integrity.

## Known Issues

No unresolved local engineering defect was found within this hardening scope. This result does not
complete the full B2 Gate: real Qwen resume, five real Checkpoints, exact next-model-input matching
and branch-isolation evidence remain future work. Integrity hashes are consistency checks, not
signatures against an attacker able to replace every artifact and hash.

## Acceptance Checklist

- [x] Runtime serialization is strict, lossless for valid JSON and fail-closed otherwise.
- [x] Model Call, aggregate Token, latency and Agent step boundaries are enforced.
- [x] Tool Call exhaustion retains the actual final-answer generation semantics.
- [x] Budget rejection occurs before target Workspace/database mutation.
- [x] Optional latest-operation omission verifies and restores with a stable hash.
- [x] Present latest-operation artifacts remain mandatory and tamper-checked.
- [x] Checkpoint v1 and v2 compatibility tests pass.
- [x] Agent State and full repository regressions pass.
- [x] Frozen Protocol, Schema, labels, limits and splits remain unchanged.
- [x] No remote or high-resource operation was performed.

## Remaining Work and Day 3 Preconditions

The hardened local Checkpoint substrate is ready for Day 3 engineering after user approval. Day 3
still needs lifecycle capture at the precise post-observation/pre-generation boundary, safe Agent
reconstruction, an intercepted recording-model request, exact first resumed model-input comparison
and proof that a completed Tool Call is not repeated. Remote Qwen or shared-GPU activity remains a
separate, explicitly approved step and is not claimed here.

## Status: PASS

All hardening requirements and local re-acceptance checks passed. Stop after this local Day 2
submission; do not begin Day 3 automatically.

## Git Commit

This report, implementation and tests are intended for the dedicated local commit:
`fix: harden checkpoint v2 runtime validation`.
