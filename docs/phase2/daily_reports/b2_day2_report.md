# B2 Day 2 Report

## Objectives

- Implement the frozen Complete Agent Checkpoint schema `2.0.0` without changing Protocol v2.
- Integrate Day 1 `AgentStateSnapshot` with Environment and Runtime state.
- Preserve v1 snapshot/restore behavior and add explicit v1/v2 manifest dispatch.
- Provide atomic v2 snapshot, complete pre-restore verification, isolated restore and six hashes.

## Local Environment Work

- Audited the existing v1 manager, TaskEnvironment/TaskSpec, RuntimeState/BudgetState and Day 1
  AgentStateAdapter against `docs/phase2/checkpoint_schema_v2.json`.
- Added strict v2 manifest validation for required/extra fields, constants, types, SHA-256 values,
  safe relative artifact paths and capture invariants without adding a new system dependency.
- Added explicit caller metadata for identities, model/Token usage, four Adapter availability
  states, resource usage, side effects and latest completed operation.
- Implemented complete local artifact capture, verification and restore using staging directories
  and atomic rename.

## Remote Environment Work

None. No SSH, HTTPS model request, vLLM operation, GPU inspection or remote resource use was
performed. All tests use a local model stub whose `generate()` method fails if called.

## Work Completed

### Architecture change

v1 remains an Environment + Runtime checkpoint with integer schema version `1`. v2 is a separate
complete checkpoint containing an immutable Agent snapshot, normalized Environment state,
extended Runtime metadata, identity configuration, six hashes and a registered artifact list.
`snapshot()`/`restore()` remain unchanged; `snapshot_v2()`/`verify_v2()`/`restore_v2()` are new.

`parse_checkpoint_manifest()` dispatches v1 and v2 without upgrading or relabeling v1 data. The
v1 restore path explicitly rejects v2 and directs callers to `restore_v2()`.

### v2 artifact layout

```text
manifest.json
agent/
  agent_state.json, messages.json, memory_steps.json, latest_action_step.json
  system_prompt.json, task_prompt.json, tool_calls.json, tool_observations.json
  tool_registry.json, next_model_input.json
environment/
  workspace_manifest.json, files/, sqlite_backup.sqlite3
  sqlite_logical_dump.json, mock_api_state.json, file_state.json
  database_state.json, side_effects.json, pending_operation.json
  latest_completed_operation.json
runtime/
  runtime_state.json, tool_history.json, tool_adapter_state.json
  fault_adapter_state.json, budget_state.json, resource_usage.json
  execution_position.json
identity/
  identity_config.json
```

Every file except `manifest.json` is registered once with safe relative path, SHA-256 and byte
size. Missing, extra, modified, duplicate, traversing or symlink artifacts are rejected.

### Environment and Runtime behavior

- Workspace capture rejects symlinks and excludes absolute path, mtime and inode from hashes.
- SQLite uses the Backup API plus `PRAGMA integrity_check`.
- Logical dump enumerates all user tables, records schema/primary keys and sorts rows by primary
  key; tables without a primary key receive deterministic canonical row ordering.
- Mock API state is cross-checked against the SQLite logical dump.
- Runtime preserves the original RuntimeState plus Tool History, extended budget, resource use,
  execution position and explicit Tool/Fault Adapter availability state.
- Prompt/Completion Tokens remain `null` when not observed; they are never inferred as zero.

### Six hashes

1. `agent_state_hash`: reused unchanged from Day 1.
2. `next_model_input_hash`: reused unchanged from Day 1 canonical messages.
3. `environment_state_hash`: workspace paths/content hashes, SQLite logical state and backup
   checksum, Mock API, side effects, pending operation and latest completed operation.
4. `runtime_state_hash`: complete RuntimeState, ordered Tool History, Tool/Fault Adapter state,
   extended budget, resource usage and execution position.
5. `identity_config_hash`: model/Worker, Prompt/tool/verifier versions and content/schema hashes,
   catalog/split hashes and Checkpoint schema version.
6. `combined_state_hash`: exactly the frozen canonical formula over schema version and the first
   five hashes.

All component hashes use Day 1 UTF-8, sorted-key, compact canonical JSON with explicit nulls and
no `default=str` fallback in v2.

## Files Changed

- `src/risk_agent/checkpoint/schema.py`
- `src/risk_agent/checkpoint/manager.py`
- `src/risk_agent/checkpoint/__init__.py`
- `src/risk_agent/checkpoint/metadata.py` (new)
- `src/risk_agent/checkpoint/v2_manager.py` (new)
- `tests/test_checkpoint_v2.py` (new)
- `docs/phase2/daily_reports/b2_day2_report.md` (new)

No frozen Protocol, risk label, feature allowlist or Task split file was modified.

## Artifacts Generated

- `CheckpointManifestV2`, `ArtifactRecordV2` and strict manifest parser.
- `CheckpointIdentityV2`, `RuntimeCheckpointMetadata`,
  `EnvironmentCheckpointMetadata`, `CaptureInvariants` and Adapter state metadata.
- `SnapshotV2Result`, `VerifyV2Result` and `RestoreV2Result`.
- Eighteen focused Checkpoint v2 tests using temporary synthetic fixtures only.

## Snapshot/Restore Example

```python
agent_snapshot = AgentStateAdapter.capture(agent)
captured = manager.snapshot_v2(
    checkpoint_id=checkpoint_id,
    environment=environment,
    runtime_state=runtime_state,
    agent_state_snapshot=agent_snapshot,
    identity=identity_metadata,
    runtime_metadata=runtime_metadata,
    environment_metadata=environment_metadata,
    capture_invariants=capture_invariants,
)

manager.verify_v2(checkpoint_id=checkpoint_id, environment=restore_environment)
restored = manager.restore_v2(
    checkpoint_id=checkpoint_id,
    environment=restore_environment,
)
```

Day 2 returns a verified `AgentStateSnapshot`; rebuilding a model/tool-backed Agent is deliberately
reserved for a later Gate.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_checkpoint.py
.venv/bin/python -m pytest -q tests/test_checkpoint_v2.py
.venv/bin/python -m pytest -q tests/test_checkpoint.py tests/test_checkpoint_v2.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src/risk_agent
git diff --check
```

## Test Results

- v1 Checkpoint regression: `7 passed in 0.50s`.
- v2 focused suite: `18 passed in 0.83s`.
- Combined v1/v2 Checkpoint suites: `25 passed in 1.10s`.
- Final full repository regression: `110 passed in 8.95s`.

Tests cover v1/v2 dispatch, strict Manifest validation, exact Agent artifacts, Tool Call IDs,
Observation, zero/null, all four Adapter-state modes, generic SQLite logical export, Mock API and
idempotency state, six hashes, atomic restore, overwrite refusal, missing/modified/extra/symlink
artifacts, manifest/hash/path traversal corruption, SQLite corruption, missing Observation and
split refusal.

## Acceptance Checklist

- [x] v1 Manifest and existing snapshot/restore remain operational.
- [x] v2 Manifest strictly maps to the frozen JSON Schema.
- [x] Agent artifacts and Day 1 hashes remain exact.
- [x] Workspace, SQLite backup/logical dump, Mock API and side effects are complete.
- [x] Runtime, budget missingness, Adapter state and execution position are complete.
- [x] All six component/combined hashes verify.
- [x] Artifact references, paths, checksums, sizes and closed file set verify.
- [x] Corruption is rejected before target Workspace mutation.
- [x] Snapshot is staged, validated and atomically installed without overwrite.
- [x] Synthetic no-split smoke tasks are rejected as complete v2 records.
- [x] Historical regression suite passes.
- [x] Remote Qwen/GPU work was not executed.

## Known Issues

- Day 2 does not rebuild or resume a ToolCallingAgent; lifecycle capture and exact intercepted
  first model request belong to Day 3.
- Adapter state export is intentionally explicit caller metadata because current adapters do not
  share a trustworthy serialization API. Unknown state cannot be represented as empty state.
- Integrity checks detect accidental corruption and inconsistent tampering. They are checksums,
  not a cryptographic signature against an attacker able to replace every artifact and hash.
- Provider transport `ChatMessage.raw` remains excluded according to the Day 1 design.

## Remaining Work

Stop after Day 2 and wait for user review. Day 3 may implement the lifecycle capture boundary,
recording-model spy, Agent rebuild and no-repeat Tool Call proof only after approval. Remote Qwen
and GPU work remain prohibited.

## Status: PASS

Complete v2 artifact capture/verify/restore, six hashes, corruption rejection and v1 regression
meet the Day 2 Gate. This status does not claim that Day 3 lifecycle or complete local B2 Gate has
passed.

## Git Commit

This report, code and tests are committed together in a dedicated Day 2 commit. Resolve its
non-self-referential SHA with:

```bash
git log -1 --oneline -- docs/phase2/daily_reports/b2_day2_report.md
```
