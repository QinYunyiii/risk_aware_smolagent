# B2 Day 4 Report

## Objectives

- Count every request delegated to the underlying model against the frozen Model Request budget,
  including requests that fail after delegation.
- Restore one complete Checkpoint v2 twice and prove equal initial hashes, independent physical
  storage and independent Runtime/Agent mutable state.
- Prove bidirectional branch isolation with real file, SQLite, RuntimeState and Agent mutations.
- Prove the source Checkpoint remains byte-for-byte unchanged.

## Local Environment Work

- Repository: `/home/three/projects/risk-aware-smolagents`.
- Baseline/current HEAD: `d08ee001ee73d0eaffef72c1a3a380215fc113b7`.
- Branch tracking display: `master...origin/main`; no branch switch performed.
- Python: 3.12.3 through `.venv/bin/python`.
- smolagents: 1.26.0.
- Initial working tree: clean.
- Successful evidence run: `runs/phase2_b2_day4/20261121T162757+0800/`.

## Remote Environment Work

None. Qwen requests: 0; SSH/API operations: 0; GPUs: 0; vLLM operations: 0; Docker experiments:
0. All run evidence is marked local scripted, non-formal and research-dataset-ineligible.

## Work Completed

### Model Request budget

`RecordingModel.model_calls_used` now equals `prior_model_calls + executed_calls`. The increment of
`executed_calls` remains immediately before underlying-model delegation, so provider exceptions
consume one request while validation rejection and valid interception consume none.

The restored boundary test sets usage to 9/10, executes one provider request that raises, observes
usage 10/10, and verifies the next attempt is rejected before the provider can run again. Normal
success, invalid request and intentional interception counters are also asserted.

### Exact JSON lifecycle restoration

The first isolation execution found that sorting every nested key while writing
`agent/agent_state.json` changed Tool Call argument insertion order. smolagents includes the Python
dictionary rendering of those arguments in its next model-visible message, so the rebuilt input
was not byte exact. The v2 writer now preserves the authoritative Agent snapshot's key order while
leaving the schema, hash definitions and other sorted artifacts unchanged. Both independent Agent
restores now pass the existing exact-next-input integrity check.

### Independent restore

Checkpoint ID: `day4_isolation_checkpoint`. Restore A and Restore B both succeeded from that same
source checkpoint.

| Hash | Restore A = Restore B | Value |
|---|---:|---|
| Agent State | yes | `7a717b45...ffe8760` |
| Next Model Input | yes | `6ec7a16a...dedfa26` |
| Environment | yes | `2d467960...aae3e2` |
| Runtime | yes | `a6e76ef8...14bd3d` |
| Combined State | yes | `8f81ef0b...2f3dbe` |

The combined state value equals the source Manifest. Workspaces and SQLite paths differ;
`samefile` is false. Both databases share device 2096 but have different inodes, 49971 and 49975.
Initial Workspace bytes and SQLite logical dumps match, and both integrity checks return `ok`.

### Branch isolation

- A modified an existing file, created a file, deleted a file and created an A-only sentinel; B
  retained its complete initial file state.
- A committed order status/version and idempotency-key changes; B retained its initial database.
- A mutated budget usage, Tool State, Agent Memory and Tool History; B retained its baseline.
- A mutated rebuilt Agent State, Observation, internal message list and Tool Call arguments; B
  retained its baseline.
- B then performed independent file, SQLite, Runtime and Agent mutations; the full post-A state
  remained unchanged.
- Runtime, Budget, histories, tool state, snapshot payload, Memory Steps, Tool Calls, canonical
  messages, generation configuration and rebuilt Agent containers are distinct instances.

### Checkpoint immutability

The before and after inventories each contain 30 files. Their canonical path/size/SHA-256
inventory digest is `5c4b72fa...ecf0d2` in both cases. `manifest.json` remains 9,492 bytes with
SHA-256 `b2322b4b...a3429`. The full inventories are exactly equal; no source artifact changed.

## Files Changed

### Modified

- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/checkpoint/v2_manager.py`
- `tests/agent/test_checkpointable_agent.py`

### Added

- `tests/test_checkpoint_v2_isolation.py`
- `docs/phase2/b2_day4_branch_isolation_design.md`
- `docs/phase2/daily_reports/b2_day4_report.md`
- eight ignored local evidence JSON files under the successful run directory

Frozen protocol, schema, budgets, labels, task templates and splits were not changed. Historical
BranchRunner and `scripts/run_day4_faults.py` were not changed.

## Artifacts Generated

The successful run contains `run_manifest.json`, `restore_hash_comparison.json`,
`storage_isolation_audit.json`, `runtime_isolation_audit.json`,
`agent_state_isolation_audit.json`, `cross_talk_audit.json`,
`checkpoint_immutability_audit.json` and `test_summary.json`, plus the actual checkpoint and two
mutated branch workspaces. The run directory is ignored by Git under the repository's data policy.

## Commands and Tests

```text
.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_checkpointable_agent.py
16 passed in 0.95s; 0 failed; 0 skipped

.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint_v2.py
33 passed in 1.38s; 0 failed; 0 skipped

.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint_v2_isolation.py
6 passed in 0.82s; 0 failed; 0 skipped

.venv/bin/python -m pytest -q --disable-warnings tests/test_branching.py
2 passed in 0.63s; 0 failed; 0 skipped

.venv/bin/python -m pytest -q --disable-warnings
159 passed in 10.47s; 0 failed; 0 skipped

.venv/bin/python -m compileall -q src tests
no output; exit 0

git diff --check
no output; exit 0
```

## Test Results

All required targeted and historical suites pass. No test was removed, weakened, skipped or
marked with a fixed PASS. The new isolation suite performs actual file operations, committed
SQLite transactions and nested Runtime/Agent mutations. The full suite increased from the Day 3
baseline of 151 tests to 159 tests.

## Acceptance Checklist

- [x] Failed delegated Model Requests consume budget.
- [x] A failed request at 9/10 prevents an eleventh underlying request.
- [x] Restore A and Restore B succeed independently from one Checkpoint v2.
- [x] Five restored hashes are equal and Combined State equals the source.
- [x] Workspace and SQLite physical storage are independent.
- [x] RuntimeState and nested mutable containers are independent.
- [x] Agent snapshot, Memory, messages, Tool Calls and state are independent.
- [x] A-to-B cross-talk is absent for file, SQLite, Runtime and Agent mutations.
- [x] B-to-A cross-talk is absent.
- [x] Original Checkpoint path/size/SHA-256 inventory is unchanged.
- [x] Full regression, compileall and diff checks pass.
- [x] Qwen/GPU/SSH/vLLM/Docker usage is zero.

## Known Issues

No Day 4 core blocker remains. The two earlier ignored local scratch run directories
`20261121T162624+0800` and `20261121T162709+0800` contain incomplete audit attempts caused by an
audit-printing query/serialization error; they are not cited as accepted evidence and were not
deleted under the append-only data policy.

This result is limited to the local deterministic fixture. It is not evidence for real-Qwen
behavior or formal benchmark validity.

## Remaining Work

- The B2 Complete Checkpoint Gate still requires the acceptance protocol's real-Qwen checkpoint
  evidence; Day 4 alone does not satisfy it.
- B3 CONTINUE labels/actions and B4 8--12 task smoke remain out of scope and unstarted here.
- Before any real-Qwen test, re-audit the remote API/TLS configuration and obtain explicit approval
  for shared-GPU/resource use as required by `AGENTS.md`.

## Status: PASS

All Day 4 core acceptance conditions pass. Stop after Day 4 and wait for user review. This status
does not mean B2, B3, B4 or formal risk-data collection is complete.

## Git Commit

No commit was requested or created. Changes remain uncommitted on
`d08ee001ee73d0eaffef72c1a3a380215fc113b7`; no Push was performed.

## Post-Commit Note — 2026-11-21

The Git section above is the accurate point-in-time status when the original Day 4 acceptance
report was written. The accepted Day 4 files were subsequently committed and pushed as
`52e97e7b37b3d88b65acbd8cffcad644dff232c1` (`feat: harden checkpoint restore branch isolation`).
This note does not rewrite the original test results or Gate. Later multi-step resume and deep
TaskSpec hardening are recorded separately in `b2_day4_post_audit_hardening_report.md`.
