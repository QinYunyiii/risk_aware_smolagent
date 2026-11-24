# P2-D2 Full Five-Task Gate (1024-token) — Stage A Report

## Objectives

- Prepare an independent, versioned 1024-completion-token five-task Gate.
- Preserve the historical 256-token Gate and both T1 diagnostic entry points.
- Freeze task order, Worker mapping, budgets, experiment identity, and research-data isolation.
- Validate success, fail-stop, truncation, no-retry, no-failover, audit, restore, and T3 idempotency behavior locally.
- Do not connect to SSH, GPU, vLLM, HTTPS model endpoints, or real Qwen during Stage A.

## Local Environment Work

- Added `configs/p2_d2_full_gate_1024.yaml` with frozen protocol, Checkpoint Schema 2.0.0, five-task order, Worker schedule, budgets, controls, research flags, and bound configuration/catalog hashes.
- Reused `configs/remote_model_p2_d2_1024.yaml`; no duplicate model configuration was created.
- Added strict full-Gate profile loading and validation alongside the existing 1024-token T1 profile.
- Added `scripts/run_p2_d2_full_gate_1024.py` with only `local-preflight` and `full-five-task-gate-1024` modes.
- Extended the shared Gate runner with versioned full-Gate metadata while preserving the unversioned historical Gate output behavior.
- Classified provider-audit persistence failures explicitly and stopped before local tool execution.
- Added local synthetic lifecycle tests using the real Agent, tools, Checkpoint v2, independent Restore, Replay Guard, Provider Audit, and two configured Worker endpoints through an in-process transport.

## Remote Environment Work

None. No SSH connection, tunnel, GPU query, vLLM operation, HTTPS preflight, or real model request was made.

## Work Completed

- The full-Gate configuration is independently identified as:
  - Experiment: `p2_d2_full_gate_1024`
  - Experiment config: `p2-d2-full-gate-1024-v1`
  - Generation config: `qwen3-8b-completion-1024-v1`
- Historical 256-token configuration hashes, verified T1 1024-token configuration hash, verified 1024-token remote-model configuration hash, catalog file hash, and canonical catalog hash are enforced.
- The frozen schedule is `T1/worker-0`, `T2/worker-1`, `T3/worker-0`, `T4/worker-1`, `T5/worker-0`.
- Execution is serial, one attempt per task, stops at the first failure, and permits no retry, replacement, skip, or Worker failover.
- Source and Restore use the same Worker; Restore performs exactly one controlled model request and does not execute its returned tool call.
- The synthetic all-five-task Gate completed five Checkpoints, five independent Restores, five six-hash matches, five exact next-input matches, and five replay blocks.
- Provider audit recorded all 15 synthetic provider responses and all 15 parsing outcomes with the delegated 1024-token limit, complete Usage, response/input hashes, and latency; the Gate summary reports per-task costs and five-task totals.
- T3 committed exactly one `update_order`, restored `order-83001` as `processing` at version 2, retained one idempotency key, and blocked replay before execution.
- T1 1024-token malformed/truncated output stopped the Gate without T2 or retry.
- A T3 failure retained completed T1/T2 artifacts and did not start T4/T5.
- A Worker-1 service failure did not fail over to Worker-0.
- Provider Audit persistence failure was fail-closed and categorized as `audit_persistence_failure` before any tool call.

## Files Changed

- `configs/p2_d2_full_gate_1024.yaml`
- `scripts/run_p2_d2_full_gate_1024.py`
- `src/risk_agent/collection/p2_d2_completion_budget.py`
- `src/risk_agent/collection/p2_d2_real_qwen_gate.py`
- `tests/test_p2_d2_full_gate_1024.py`
- `docs/phase2/daily_reports/p2_d2_full_gate_1024_stage_a_report.md`

## Artifacts Generated

- Local-preflight manifests and synthetic Gate artifacts were generated only in pytest temporary directories.
- No formal experiment Run, training label, real-Qwen result, remote artifact, or raw audit data was added to Git.
- The new entry point writes future runs under `runs/phase2_p2_d2_full_gate_1024/<run_id>/`, refuses an existing Run directory, and produces an SHA-256 artifact manifest.

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_p2_d2_full_gate_1024.py tests/test_p2_d2_completion_budget_revision.py tests/test_p2_d2_real_qwen_gate.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

The local-preflight CLI was also executed by a subprocess test with an isolated temporary output root. The test verified `network_requests_made: 0`, fixed task/Worker selection, authorization fail-closed behavior, rejection of arbitrary task CLI arguments, and every artifact-manifest SHA-256 value.

## Test Results

- New full-Gate test cases: 10 passed, increasing the collected suite from 298 to 308 tests.
- Full regression suite: `308 passed in 32.83s` (baseline was 298 tests).
- Compile check: PASS.
- Diff whitespace check: PASS.
- Remote calls: 0.
- Real Qwen calls: 0.

## Acceptance Checklist

- [x] Independent full-Gate experiment/config identities.
- [x] Historical 256-token and verified 1024-token hashes bound and unchanged.
- [x] Catalog, split-derived task selection, five task IDs, order, and Worker schedule frozen.
- [x] 1024 completion-token cap and all remaining budgets frozen.
- [x] Only two public CLI modes; no arbitrary task, limit, or count parameters.
- [x] Remote mode requires both explicit authorization and validated remote evidence before creating a Run directory.
- [x] Strict serial one-attempt execution with stop-on-first-failure.
- [x] No retry, replacement, skip, or Worker failover.
- [x] Source/Restore Worker identity and budget continuity retained.
- [x] Five-task synthetic success path verified end-to-end.
- [x] T1 truncation and T3 failure stop paths verified.
- [x] T3 exactly-once write, restored version, idempotency, and replay protection verified.
- [x] Provider response/usage/parsing audit and audit-persistence fail-closed path verified.
- [x] Per-task Token/Latency reporting and five-task aggregate costs verified.
- [x] Existing Run IDs are rejected and existing artifact manifests remain unchanged.
- [x] Research flags remain non-formal and ineligible for training data.
- [x] Full local regression and compilation passed.
- [x] No Stage B or remote operation performed.

## Known Issues

- The real five-task Qwen Gate has not run in this stage and remains `BLOCKED`.
- Stage A synthetic results validate the engineering chain only; they are not formal experiment results and do not create `Y_continue` training labels.
- A future Stage B run still requires fresh SSH/TLS/Worker/GPU/model-identity evidence and the exact user authorization specified below.

## Remaining Work

- Wait for the exact authorization: `授权执行 P2-D2 1024-token Full Five-Task Real-Qwen Gate`.
- After authorization, revalidate SSH, TLS, both Workers, GPU mapping/availability, model identity, and frozen remote evidence before any model request.
- Run the new five-task Gate once under its unique Run ID, preserve all raw artifacts, produce the Stage B report, and stop for review.

## Status: PASS

Stage A local engineering and acceptance are complete. The full real-Qwen five-task Gate itself remains `BLOCKED` pending separate Stage B authorization.

## Git Commit

- Baseline: `9e9763b52522b14605f2100051ef54aaaf1da206`
- Prepared commit message: `feat: prepare P2-D2 1024-token five-task gate`
