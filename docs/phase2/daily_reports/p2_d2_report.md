# P2-D2 Report

## Objectives

- Audit the existing Checkpoint v2 lifecycle against the P2-D1 multi-step catalog.
- Capture a real local Agent decision point after dependent tool Observations enter
  memory and before the next model request or verifier.
- Prove independent restore, six-hash equality, exact first restored input, and no
  replay across all four task domains.
- Preserve historical v1/v2 checkpoint compatibility and hidden-information isolation.

## Local Environment Work

All implementation and validation ran in the repository `.venv` under WSL. The
compatibility test uses the real `CheckpointableToolCallingAgent`, smolagents step
lifecycle, local benchmark tools, `DecisionCheckpointCapture`, and
`CheckpointManager.snapshot_v2()/restore_v2()`. The provider is a deterministic local
scripted model so no remote model request is made.

The four selected new Train tasks are:

- `p2_data_region_units_sum_001`
- `p2_file_report_control_001`
- `p2_api_process_control_001`
- `p2_logic_chain_control_001`

Each task executes two tools. The second tool's arguments are resolved from the first
tool's actual Runtime Tool History result before checkpoint capture.

## Remote Environment Work

None. No SSH, HTTPS model API, Docker Trial, GPU, Qwen worker, vLLM service, tunnel,
or remote process was accessed or changed. The real-Qwen gate was not authorized.

## Work Completed

1. Reused the existing capture and v2 snapshot/restore implementations after audit.
2. Synchronized `RuntimeState.agent_memory` from the authoritative Agent snapshot at
   the accepted capture boundary, with rollback if snapshot creation fails.
3. Added task seed to new checkpoint identity and identity configuration hashing.
4. Preserved schema `2.0.0` verification for historical v2 manifests without an
   explicit identity seed; Runtime seed validation remains mandatory.
5. Added four-domain, new-Train-template lifecycle coverage after two dependent tool
   calls, including actual file and SQLite state restoration.
6. Verified completed tool calls are rejected by the replay guard before any repeated
   read or write, history append, budget charge, or environment mutation.
7. Documented the frozen decision boundary, state authorities, hash contract,
   no-replay rule, compatibility behavior, and separate real-Qwen gate.

## Files Changed

- `src/risk_agent/collection/decision_checkpoint.py`
- `src/risk_agent/checkpoint/schema.py`
- `src/risk_agent/checkpoint/v2_manager.py`
- `tests/test_model_backed_checkpoint.py`
- `docs/phase2/checkpoint_protocol.md`
- `docs/phase2/daily_reports/p2_d2_report.md`

## Artifacts Generated

No persistent experiment run was generated. Pytest creates isolated temporary
workspaces and checkpoint artifacts for each domain and removes them after the run.
Historical B2/B3/B4 and P2-D1 artifacts were not changed or overwritten.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q --disable-warnings \
  tests/agent/test_checkpointable_agent.py \
  tests/test_checkpoint_v2.py \
  tests/test_checkpoint_v2_isolation.py
.venv/bin/python -m pytest -q --disable-warnings \
  tests/test_model_backed_checkpoint.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

## Test Results

- Existing checkpoint/agent regression set: 100 passed.
- P2-D2 four-domain compatibility set: 4 passed.
- Full repository suite: 257 passed in 24.67 seconds.
- Compileall: passed.
- `git diff --check`: passed.

For every P2-D2 domain case:

- two real local task-tool calls completed through the Agent lifecycle;
- the second call consumed the first call's actual result;
- the second Observation was present in the latest ActionStep and Agent Memory;
- exactly two model calls had completed and the third had not started at capture;
- the verifier had not run and continuation budgets remained;
- Agent, Environment, and Runtime states restored independently;
- all six hashes matched exactly;
- the reconstructed first next-model input matched exactly and was intercepted before
  provider execution;
- a repeated completed call was blocked before execution;
- the source checkpoint inventory and hashes remained unchanged;
- a mismatched Validation split was rejected;
- evaluator reference steps and expected output were absent from Agent state;
- historical v2 manifests without identity seed remained parse-compatible.

## Hash Results

Across 4/4 cases, the following restored values matched their frozen manifest values:

- `agent_state_hash`: 4/4
- `next_model_input_hash`: 4/4
- `environment_state_hash`: 4/4
- `runtime_state_hash`: 4/4
- `identity_config_hash`: 4/4
- `combined_state_hash`: 4/4

The canonical P2-D1 v2 catalog hash remained:
`c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c`.

## Acceptance Checklist

- [x] Existing Checkpoint v2 implementation audited and reused.
- [x] New P2-D1 multi-step templates enter the actual Agent execution path.
- [x] Actual dependent tool Observations are captured in Agent Memory.
- [x] Agent / Environment / Runtime state is complete and independently restorable.
- [x] Six hashes match after restore in all four domains.
- [x] Completed tool calls are not replayed.
- [x] Hidden answers and future evaluator data remain isolated.
- [x] Historical v1/v2 compatibility is retained by the full regression suite.
- [x] Train-only selection and mismatched-split rejection are covered.
- [x] Focused tests pass.
- [x] Full repository suite passes (257 tests).
- [x] Compileall passes.
- [x] `git diff --check` passes.
- [ ] Real-Qwen gate pending separate authorization.

## Known Issues

- The local scripted provider proves Agent/checkpoint engineering compatibility, not
  Qwen3-8B's empirical ability to complete the new task prompts.
- Historical schema `2.0.0` manifests may omit seed from the identity section. They
  remain verifiable through their original identity hash plus mandatory Runtime seed
  matching; every newly written checkpoint includes seed explicitly.
- No P2-D2 output is eligible as formal risk-model training data.

## Remaining Work

After user review and separate authorization, run the real-Qwen P2-D2 gate on at
least five new Train multi-step tasks covering all four domains. That gate must use
fresh append-only run IDs, preserve TLS verification, record real token/latency data,
and achieve 5/5 restore and exact-next-input matches.

Do not begin P2-D3 as part of this task.

## Status: CONDITIONAL PASS

The local engineering gate passed. The required real-Qwen gate has not been authorized
or executed, so P2-D2 cannot be reported as full PASS.

## Git Commit

Planned commit message: `feat: validate P2-D2 multi-step checkpoints`. The final
commit SHA and push result will be recorded in the handoff after all local gates pass.
