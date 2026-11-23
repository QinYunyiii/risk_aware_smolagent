# B3 Model-backed CONTINUE & Verifier Gate Report

Date: 2026-11-23 (Asia/Shanghai)
Run ID: `b3_five_continue_gate_20261123_001`
Source Run ID: `b2_day5_five_gate_20261123_001`
Protocol: `risk-continue-v2.0.0`
Execution mode: `model_backed_smoke`
Controller: `qwen3_model`
Formal experiment: `false`
Research dataset eligible: `false`

## Objectives

- Restore the five accepted B2 Day 5 Checkpoints without modifying them.
- Call the real Qwen-backed `agent.resume(stream=True)` lifecycle through Agent termination.
- Run the deterministic verifier only after termination and produce one traceable `Y_continue` per Checkpoint.
- Demonstrate exact first-input recovery, budget continuity, replay prevention, branch isolation, and complete cost observability.
- Keep the exercise limited to CONTINUE. RETRY, REPLAN, B4, and formal data collection are out of scope.

## A. Git Status

- Baseline commit: `2d9125687f7647a1c186d1263810e932867aedda`
- Current HEAD: `2d9125687f7647a1c186d1263810e932867aedda`
- Branch: `master`
- Commit / Push: not performed, as required by the B3 task.
- Modified tracked files:
  - `src/risk_agent/collection/__init__.py`
  - `src/risk_agent/collection/benchmark_tools.py`
- New B3 implementation and test files:
  - `src/risk_agent/collection/continue_outcome.py`
  - `src/risk_agent/collection/continue_gate.py`
  - `scripts/run_b3_continue_gate.py`
  - `tests/agent/test_continue_outcome.py`
  - `tests/agent/test_b3_continue_gate.py`
  - `docs/phase2/daily_reports/b3_day_a1_checkpoint_audit.md`
  - `docs/phase2/daily_reports/b3_stage_a_report.md`
  - this report
- The pre-existing untracked `docs/phase2/daily_reports/b2_day5_real_qwen_report.md` was preserved and not treated as a B3-created file.

## B. B2 Checkpoint Source

All five source Checkpoints use schema `2.0.0`. Stage A verified every manifest and artifact before execution. A post-run independent inventory comparison found no missing, extra, size-changed, or hash-changed files.

| Checkpoint ID | Manifest SHA-256 | Files | Post-run verification |
|---|---|---:|---|
| `checkpoint_data_filter_category_eq_001_001` | `6758c96a24d6886054c3b857688c84fa944fecdec91c20b7b6350761e27f7836` | 29 | unchanged |
| `checkpoint_file_list_project_tree_001_001` | `9ca8fc2fa007368383ec3ca40158ce662934e9d3f84c74ed09cd663fd4828fd1` | 31 | unchanged |
| `checkpoint_api_inventory_primary_001_001` | `dd063c7f1b9dea772318af3e34fbeebde92b9bf3812f250bfaa49be471b52ac7` | 28 | unchanged |
| `checkpoint_logic_calculate_addition_001_001` | `93f5e83e9716ff8f10aeeef1470be3c3f63a0265d86b6f3e7f81a2bfa217b309` | 28 | unchanged |
| `checkpoint_data_filter_amount_gt_001_001` | `4b2f7fa164d8fa99b86261be608dd2ec20e5f97659895382f97849933f96f953` | 29 | unchanged |

Checkpoint compatibility: **PASS (5/5)**. All six protocol hashes (`agent_state`, `environment_state`, `runtime_state`, `identity_config`, `next_model_input`, and `combined_state`) matched for every restore.

## C. Resume Implementation

- Entry point: `CheckpointableToolCallingAgent.resume(stream=True)` is invoked by `ContinueGateRunner`; the returned stream is consumed until the Agent terminates.
- Step cursor: each Checkpoint resumes after captured step 1 and terminates at step 2; no earlier action step is re-executed.
- Model-call counter: restored from 1 and advanced to 2 for every branch.
- Tool-call counter: restored at 1 and remained 1 for every branch because Qwen emitted `final_answer` immediately; no already-completed local tool was replayed.
- Runtime budget: restored counters are retained. Each branch added exactly the observed prompt and completion tokens; there was no reset or limit bypass.
- Memory: restored Agent memory is retained and one new terminal action step is appended.
- Stream termination: all five streams ended with `final_answer`; the deterministic verifier ran afterward.
- First-request contract: the canonical serialized input and SHA-256 matched exactly for all five branches before the request was accepted.
- Replay guard: source tool-call identities are registered, and any attempt to execute a completed call raises instead of performing the side effect.

## D. Remote Model

- Worker 0: local verified endpoint `https://localhost:8000/v1` over the owned SSH tunnel; remote bind `127.0.0.1:8000`; physical GPU 0; UUID `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160`; existing PID `382420`.
- Worker 1: local verified endpoint `https://localhost:8001/v1` over the owned SSH tunnel; remote bind `127.0.0.1:8001`; physical GPU 1; UUID `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d`; existing PID `382422`.
- Model ID: `Qwen/Qwen3-8B`.
- Model version: `Qwen3-8B-modelscope-snapshot`.
- Weight identity: config, index, and all five safetensor shard SHA-256 values matched the accepted B2 evidence.
- Serving stack: vLLM `0.34.0`, PyTorch `2.14.0+cu132`, Transformers `5.22.2`.
- TLS: CA verification enabled; certificate SAN contains `localhost` and `127.0.0.1`; unauthenticated request returned 401 and authenticated model discovery returned 200.
- Request count: 5 successful authenticated requests, three on worker 0 and two on worker 1.
- Remote changes: none. No Worker was started, restarted, stopped, or reconfigured.

## E. Five-task Results

All rows restored successfully, matched all six hashes, matched their first model input exactly, completed the real Qwen stream, and ran the deterministic verifier. “New tools” excludes the terminal `final_answer` pseudo-tool.

Execution remained sequential. `data_filter_category_eq_001` first completed the full restore → exact-input → Qwen termination → verifier → outcome chain; only then did the runner continue with the remaining four Checkpoints.

| Task | Worker | Restore / Exact input | Model requests | New tools | Prompt / completion tokens | Model / wall latency (s) | Final answer summary | Verifier | `Y_continue` |
|---|---|---|---:|---:|---:|---:|---|---|---:|
| `data_filter_category_eq_001` | worker-0 | PASS / PASS | 1 | 0 | 1822 / 72 | 2.102 / 2.110 | Python-style list encoded as a string | `result=false` | 0 |
| `file_list_project_tree_001` | worker-1 | PASS / PASS | 1 | 0 | 1528 / 87 | 2.343 / 2.349 | prose containing the three correct-looking paths | `result=false` | 0 |
| `api_inventory_primary_001` | worker-0 | PASS / PASS | 1 | 0 | 1601 / 36 | 1.083 / 1.090 | prose reporting 2 units | `inventory:sku-73001=true`, `result=false` | 0 |
| `logic_calculate_addition_001` | worker-1 | PASS / PASS | 1 | 0 | 1566 / 20 | 0.827 / 0.836 | `9` | `result=true` | 1 |
| `data_filter_amount_gt_001` | worker-0 | PASS / PASS | 1 | 0 | 1817 / 70 | 1.917 / 1.924 | Python-style list encoded as a string | `result=false` | 0 |

Aggregate B3 usage: 8,334 prompt tokens, 285 completion tokens, 8.273 seconds of model latency, and 8.309 seconds of branch wall latency. Every B3 request reported token usage and latency.

Budget continuity by task:

| Task | Model calls | Tool calls | Total tokens before → after |
|---|---|---|---|
| `data_filter_category_eq_001` | 1 → 2 | 1 → 1 | 1720 → 3614 |
| `file_list_project_tree_001` | 1 → 2 | 1 → 1 | 1432 → 3047 |
| `api_inventory_primary_001` | 1 → 2 | 1 → 1 | 1537 → 3174 |
| `logic_calculate_addition_001` | 1 → 2 | 1 → 1 | 1517 → 3103 |
| `data_filter_amount_gt_001` | 1 → 2 | 1 → 1 | 1719 → 3606 |

## F. Failure Analysis

- Agent failures: 0.
- Tool failures: 0.
- Model-service failures: 0.
- Environment failures: 0.
- Budget exhaustion: 0.
- Verification failures: 4. The deterministic verifier intentionally compares the candidate result to the frozen expected value without coercion. Qwen returned string/prose representations for the two filter tasks, the file-list task, and the inventory result; those representations did not equal the frozen structured/scalar result, so the labels are correctly recorded as 0. No success standard or task definition was changed.
- Null outcomes: 0.
- Failed attempts removed from statistics: 0; `failures.jsonl` is present and empty.

These four verifier failures are observed model outcomes, not incomplete chains. Each has a terminal answer, verifier record, failure category, and complete lineage.

## G. Side Effect Audit

- Completed source tool replay: **0**.
- New non-terminal local tool calls during B3: **0**.
- Tool lineage completeness: **5/5**.
- SQLite isolation: five branch-local database files have five distinct filesystem inodes and branch-specific absolute paths.
- Workspace isolation: every branch uses a unique branch-attempt directory; file-backed tasks were restored into only their own workspace.
- Branch cross-talk: **0 detected**.
- Checkpoint mutation: **0**. The independent post-run comparison matched every source file against the Stage A inventory.
- Artifact integrity: `artifact_manifest.json` declares 46 artifacts; all 46 exist and match recorded size and SHA-256.

## H. Label Quality

- `Y_continue=1`: 1.
- `Y_continue=0`: 4.
- `Y_continue=null`: 0.
- Label lineage complete: 5/5 (100%).
- Prompt-token missing rate: 0%.
- Completion-token missing rate: 0%.
- Model-latency missing rate: 0%.
- Tool-latency missing rate: 0%. No new non-terminal tools were invoked; the explicit zero-tool path is recorded and treated as observed, not missing.
- Wall-latency missing rate: 0%.

These five labels prove the engineering chain can produce real model-backed CONTINUE labels. They are B2 Day 5 Smoke derivatives and are not eligible for Train, Validation, Test, threshold tuning, or formal risk-model training.

## I. Resource Release

- Tunnels created by this run: two SSH ControlMaster tunnels, local PIDs `580412` and `580421`.
- Tunnel cleanup: both owned tunnels stopped; local ports 8000 and 8001 confirmed closed.
- Remote processes created by this run: none.
- Existing Workers retained: PIDs `382420` and `382422` were still alive after tunnel cleanup.
- Final read-only GPU check: 2026-11-23 17:08:31 Asia/Shanghai. GPU 0 and 1 retained the same UUID mapping, each used 21,777 MiB, and each reported 0% utilization at the sample instant.
- Other users' GPUs and processes were not modified.

## Commands and Tests

```text
.venv/bin/python scripts/run_b3_continue_gate.py --mode five-continue-gate --authorize-remote --run-id b3_five_continue_gate_20261123_001 --checkpoint-run runs/phase2_b2_day5/b2_day5_five_gate_20261123_001 --output-root runs/phase2_b3_continue_gate --remote-evidence runs/phase2_b3_continue_gate/b3_stage_b_remote_evidence_20261123_001.json
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests
git diff --check
```

Results:

- Real Qwen gate runner: exit success; `gate_summary.json` reports `PASS`.
- Full test suite: `242 passed in 15.41s`.
- Compile check: PASS.
- Diff whitespace check: PASS.

## Artifacts Generated

- Stage B remote evidence: `runs/phase2_b3_continue_gate/b3_stage_b_remote_evidence_20261123_001.json`
- Remote endpoint preflight: `runs/phase2_b3_continue_gate/b3_remote_preflight_20261123_001/`
- Final five-task run: `runs/phase2_b3_continue_gate/b3_five_continue_gate_20261123_001/`
- The final run contains all required manifests, branch attempts, model/tool/Agent-visible traces, outcomes, verifier results, budget audit, failure log, resource audit, Gate summary, artifact manifest, and five isolated branch directories.

## J. Final Gate

```text
B3: PASS

Five Complete Chains: 5/5

B2 Checkpoint Compatibility: PASS

Exact Input: 5/5

Qwen CONTINUE: 5/5

Verifier: 5/5

Outcome Lineage: 5/5

Tool Replay: 0

Cross-talk: 0

Formal Data Collection: NOT AUTHORIZED
B4: NOT STARTED
```

## Known Issues

- Four of five Qwen terminal answers do not match the frozen verifier's expected result type/shape and therefore correctly yield `Y_continue=0`. This is an experimental observation, not a Gate implementation defect.
- The remote container does not provide the `ss` utility. Worker survival was instead verified directly by the audited existing PIDs after tunnel cleanup; earlier authenticated HTTPS preflight had already verified both listening services.

## Remaining Work

- User review of B3 code, artifacts, and this report.
- No B4, RETRY/REPLAN extension, formal collection, commit, or push is started automatically.

## Status: PASS

## Git Commit

Not created. B3 task rules require waiting for user review before any commit or push.
