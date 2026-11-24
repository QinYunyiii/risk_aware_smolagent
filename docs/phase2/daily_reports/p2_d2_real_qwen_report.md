# P2-D2 Real-Qwen Gate Report

## Objectives

- Run the fixed five-task P2-D2 real-Qwen checkpoint/restore Gate.
- Capture only after the second successful, sequential, observation-dependent local
  tool call.
- Prove independent restore, six-hash equality, exact first restored input, explicit
  replay blocking, real restored Qwen generation, and complete cost auditing.
- Stop after the Canary on any failure and never substitute or retry a task.

## Git Status

- Starting commit: `506ade6facf5b4245f28490b36bbd523d245618a`.
- Local branch: `main`, tracking `origin/main` at the same commit.
- The initially named local branch was `master`; after explicit user authorization a
  new local `main` tracking branch was created without rewriting history.
- Starting worktree: clean.
- Raw `runs/` artifacts remain ignored and local; they are not included in Git delivery.

## Local Environment Work

Stage A independently regenerated the P2-D1 catalog in memory:

| Item | Result |
| --- | --- |
| Templates | 80 |
| Generated task instances | 160 |
| Template split | Train 48 / Validation 16 / Test 16 |
| Instance split | Train 96 / Validation 32 / Test 32 |
| Domain instances | 40 per domain |
| Canonical catalog hash | `c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c` |
| Generated task-document hash | `c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c` |
| Split manifest hash | `baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9` |
| Fixed five Task ID hash | `6b08f32a5e2d01d7df5f211530b7d20369c3bc870ca400a74a3eb26b64f21d91` |

The two catalog hashes happen to be equal because both hash the same canonical ordered
task-document list; they remain named separately from the split and fixed-selection
hashes. All fixed tasks were present, Train-only, new `p2_` templates, three-step
tasks, unique, and covered all four domains.

The new runner reuses Checkpoint v2 and adds only the P2-D2 Gate orchestration:

- fixed task selection and canary stopping;
- capture after two separate single-call ActionSteps;
- actual-result dependency validation;
- Source next-input interception before Provider execution;
- independent restore, explicit replay attempt, and six-hash checks;
- one real restored Qwen request without processing its returned Tool Call;
- append-only task, model, budget, checkpoint, restore, replay, and isolation audits.

No fault injector was instantiated. The API workflow remains the local SQLite Mock API.

## Remote Environment Work

All remote commands were read-only. No model service, remote directory, package,
weight, TLS configuration, or GPU allocation was changed.

- SSH target: `root@121.48.161.243:4035`, strict known-host checking and dedicated
  `risk_agent_audit` key, with `ClearAllForwardings=yes`.
- Actual target: Docker container `36f825bcc5da`, Ubuntu 22.04.3 LTS, root user.
- Kernel: `6.8.0-138-generic`; host operating system remains unaudited.
- GPU samples: `02:36:16`, `02:37:12`, and `02:44:36` UTC on 2026-11-24. The valid
  first/last pair is separated by over eight minutes.
- Existing Workers were proven project-owned through `/mnt/risk-aware-smolagents`
  state files, container PIDs, cmdlines, ports, model path, CUDA mapping, GPU UUID,
  and stable memory usage.

| Worker | Container PID | Physical GPU | GPU UUID | Memory | Endpoint |
| --- | ---: | ---: | --- | ---: | --- |
| worker-0 | 382420 | 0 | `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160` | 21,781 MiB | `https://localhost:8000/v1` |
| worker-1 | 382422 | 1 | `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d` | 21,779 MiB | `https://localhost:8001/v1` |

The host-visible NVIDIA PIDs differ from container PIDs because of PID namespace
isolation. GPU UUID and memory mapping were stable in all samples. Other occupied GPUs
were observed but not used or modified.

## Model and Weight Identity

- Served Model ID: `Qwen/Qwen3-8B`.
- Configured Model Version: `Qwen3-8B-modelscope-snapshot`.
- Architecture: `Qwen3ForCausalLM`; model type `qwen3`; dtype `bfloat16`.
- vLLM `0.34.0`; Torch `2.14.0+cu132`; Transformers `5.22.2`.
- `config.json`, safetensors index, and all five model shards were re-hashed. Every
  SHA-256 matched the B2 frozen identity recorded in `remote_preflight.json`.

## TLS and Tunnel Audit

- Two independent ControlMasters were created for this run only: local PIDs 782032
  and 782161, forwarding local ports 8000 and 8001.
- `ClearAllForwardings=yes` cleared configured forwards. Because it also clears an
  inline `-L`, forwarding was added afterward through the owned ControlMaster using
  `-O forward`; no unrelated tunnel was touched.
- The repository-external CA at
  `/home/three/.config/risk-aware-smolagents/pki/ca.crt` was used with strict
  certificate and hostname verification.
- Both authenticated `/v1/models` calls returned `Qwen/Qwen3-8B`.
- OpenAI client retries were zero and the smolagents retryer allowed one attempt.
- No `verify=False`, `curl -k`, fallback endpoint, or public unauthenticated port was used.

## Real-Qwen Run

Authoritative attempted Run:
`runs/phase2_p2_d2_real_qwen/p2_d2_real_qwen_20261124_001/`.

T1 `p2_data_region_units_sum_001` ran first as the mandatory Canary on worker-0:

1. Qwen produced a valid `read_csv` Tool Call.
2. The local tool succeeded and its six-row Observation entered Tool History.
3. Qwen received that Observation and attempted the dependent `filter_records` call.
4. The second response contained malformed or truncated Tool Call JSON. Parsing failed
   at line 1, column 608 (character 607), around the sixth record's `latency` field.
5. smolagents recorded `AgentParsingError`; the second tool never executed.

This is a **Model Behavior Failure**. SSH, TLS, `/v1/models`, worker identity, the
first model request, and the first local tool call were healthy. The initial raw task
record labeled the error as `task_execution_failure`; it was preserved unchanged and
an append-only `post_run_failure_audit.json` records the corrected classification.
The runner now classifies `AgentParsingError` and `AgentGenerationError` as model
behavior failures.

The Canary stopping rule was applied immediately:

- no automatic retry;
- no task replacement;
- T2–T5 were not started;
- no second successful dependent Observation existed;
- no Checkpoint was created;
- Restore, Hash, replay, and real restored-request stages were not reached.

## Checkpoint and Restore Results

| Metric | Result |
| --- | ---: |
| Fixed tasks completed | 0/5 |
| Successful dependent two-call Canaries | 0/1 |
| Eligible Checkpoints | 0/5 |
| Six-hash restore matches | 0/5, not reached |
| Exact first restored inputs | 0/5, not reached |
| Real restored Qwen requests | 0/5, not reached |
| Replay Guard probes | 0/5, not reached |
| API writes | 0 |

No zero value above is represented as a failed Checkpoint comparison: the stages are
explicitly `not_reached` because the capture precondition did not exist.

## Request, Token, Latency, and Budget Audit

- Provider Completion attempts before stopping: 2.
- Successful local tool calls: 1.
- Automatic Provider retries: 0.
- Frozen per-task limits remained 10 Agent steps, 10 model calls, 12 tool calls,
  50,000 total tokens, 256 completion tokens/request, 8,192 context tokens,
  120-second request timeout, and 900-second wall time.
- The original failure path did not persist complete per-request Usage or the complete
  malformed raw response before process exit. Prompt/Completion Token values are
  therefore recorded as `null`, not zero, and `usage_complete=false`.
- Usage observability is below the required 95%; this independently prevents PASS.
- The runner was hardened after the preserved Run to append the failing ActionStep,
  request hash, Usage/Latency when available, and explicit `not_reached` records for
  all downstream audit streams.

## Side Effects and Data Isolation

- Only the T1 source workspace and local SQLite fixture were created.
- The successful `read_csv` was read-only.
- No `update_order`, external API call, Checkpoint restore, replay, or branch occurred.
- No hidden answer, reference step, or formal label was supplied to Qwen.
- All records carry `formal_experiment=false`, `execution_mode=model_backed_smoke`,
  `research_dataset_eligible=false`, and null action labels.
- This Run is excluded from formal Train/Validation/Test collection and model training.

## Artifacts Generated

The Run includes the required named audit streams. Downstream streams contain explicit
`not_reached` records rather than fabricated empty successes. The original
`artifact_manifest.json` is preserved; additions from post-run classification and
resource audit are covered by `artifact_manifest_post_audit.json`.

Key artifacts:

- `run_manifest.json`
- `remote_preflight.json`
- `task_selection.json`
- `task_attempts.jsonl`
- `tool_trajectories.jsonl`
- `decision_checkpoints.jsonl`
- `restore_audit.jsonl`
- `model_request_audit.jsonl`
- `budget_audit.jsonl`
- `checkpoint_inventory.jsonl`
- `replay_guard_audit.jsonl`
- `branch_isolation_audit.jsonl`
- `post_run_failure_audit.json`
- `resource_release.json`
- `gate_summary.json`
- `artifact_manifest.json`
- `artifact_manifest_post_audit.json`

No raw Run artifact is committed to Git.

## Resource Release

- Existing vLLM Workers started/stopped/restarted by this Run: 0 / 0 / 0.
- Final sample at `2026-11-24T02:48:35.398873579+00:00` showed both Worker PIDs alive,
  stable GPU memory, and 0% sampled utilization.
- Both owned ControlMasters received verified exit requests.
- Local ports 8000 and 8001 had no listeners after cleanup.
- No other GPU, process, or user tunnel was modified.

## Commands and Tests

```text
.venv/bin/python -m pytest -q --disable-warnings tests/test_model_backed_checkpoint.py
.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_checkpointable_agent.py tests/test_checkpoint_v2.py tests/test_checkpoint_v2_isolation.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/python -m pytest -q --disable-warnings tests/test_p2_d2_real_qwen_gate.py
.venv/bin/python scripts/run_p2_d2_real_qwen_gate.py --mode gate --authorize-remote ...
git diff --check
```

Results before the real Run:

- existing P2-D2 tests: 4 passed;
- checkpoint/agent regression: 100 passed;
- new P2-D2 real-Qwen tests: 4 passed;
- related combined regression: 114 passed;
- full regression after implementation: 261 passed;
- compileall and `git diff --check`: passed.

Results after failure-path hardening:

- P2-D2 real-Qwen tests: 5 passed in 1.18 seconds;
- full regression: 262 passed in 25.32 seconds;
- compileall and `git diff --check`: passed.

## Acceptance Checklist

### Task coverage

- [ ] Five fixed tasks executed (T1 stopped during its second response; T2–T5 not run).
- [ ] Four domains executed.
- [x] Selection itself contains five new Train multi-step tasks across four domains.
- [ ] Every task completed two real dependent tool calls.

### Checkpoint and model input

- [ ] Five real Checkpoints.
- [ ] 5/5 Agent, Environment, and Runtime restores.
- [ ] Six hashes 5/5.
- [ ] Exact restored first input 5/5.
- [ ] Five real restored Qwen requests.

### Side effects and cost

- [x] No historical tool replay occurred.
- [x] No API write was repeated; no API write occurred.
- [x] No automatic model retry or task substitution occurred.
- [x] Frozen budgets were not changed or exceeded.
- [ ] Token/Latency audit meets 95% completeness.
- [x] Smoke data remains excluded from formal datasets.

## Known Issues

- The Data Analysis task embeds the complete six-row first Observation in the second
  Tool Call arguments. Under a 256-token completion cap, Qwen emitted malformed or
  truncated JSON on this Canary. The frozen budget was not changed to force a pass.
- The original failure path persisted the failure type but not the complete malformed
  response or Usage. Post-run records explicitly preserve this evidence gap.
- No claim can be made about Checkpoint/Restore behavior on the real P2-D1 Catalog
  because the Canary never reached the capture boundary.

## Remaining Work

The Gate cannot be rerun automatically. Any next attempt requires explicit user review
and a new authorization. Because changing completion limits, prompt/tool schemas, task
representation, or capture protocol would be a research/design change, it also
requires explicit approval before implementation. P2-D3 must not start.

## Final Gate

```text
P2-D2 Real-Qwen Gate: BLOCKED

Fixed Tasks Completed: 0/5
Canary: MODEL BEHAVIOR FAILURE at second response
Eligible Checkpoints: 0/5
Six-Hash Matches: 0/5 (not reached)
Exact Restore Inputs: 0/5 (not reached)
Real Restore Requests: 0/5 (not reached)
Tool Replay: 0
Usage Completeness: below 95%
T2-T5: not started by stopping rule
P2-D3: NOT STARTED
```

## Status: BLOCKED

The real-Qwen Gate did not meet its core acceptance criteria. Local engineering tests
passing does not change this status.

## Git Commit

Planned commit message: `feat: add P2-D2 real Qwen checkpoint gate`. The final Commit
SHA and Push result are recorded in the handoff after final regression and sensitive
data review.
