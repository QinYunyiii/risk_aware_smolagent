# P2-D2 1024-Token T1 Real-Qwen Diagnostic Report

## Objectives

- Execute exactly one authorized real-Qwen Source attempt for the fixed T1 task under
  the separately versioned 1,024-token completion configuration.
- Determine whether the second observation-dependent `filter_records` Tool Call can
  complete without the previously confirmed 256-token truncation.
- If successful, capture a real Decision Checkpoint and verify independent Restore,
  six hashes, exact first model input, Replay Guard, and one real restored request.
- Preserve complete Provider/parsing evidence and release only resources created by
  this Run.

## Status

**P2-D2 1024-Token T1 Diagnostic: PASS**

**P2-D2 Real-Qwen Full Five-Task Gate: BLOCKED**

The 1,024-token candidate resolved the previously observed completion truncation for
this single T1 attempt: the second Provider response finished with `tool_calls` after
292 completion tokens, exposed a structured `filter_records` call, parsed successfully,
and executed locally. This result does not establish that 1,024 is optimal, does not
validate T2--T5, and cannot be treated as a formal training record.

## Git and Experiment Identity

- Executed commit: `ffd85f7d796299e4f1183dec2383155c32ad071a`.
- Branch: `main`; local HEAD and `origin/main` matched before execution.
- Starting worktree: clean.
- Run ID: `p2_d2_t1_budget_1024_20261124_001`.
- Experiment ID: `p2_d2_t1_completion_1024_diagnostic`.
- Experiment config version: `p2-d2-completion-budget-1024-v1`.
- Generation config version: `qwen3-8b-completion-1024-v1`.
- Mode: `single-t1-budget-1024-diagnostic`.
- Checkpoint protocol/schema: `risk-continue-v2.0.0` / `2.0.0`.
- Historical 256-token configuration and Runs were not modified.

Research flags remained:

```text
formal_experiment=false
execution_mode=model_backed_smoke
research_dataset_eligible=false
y_continue=null
y_retry=null
y_replan=null
```

## Remote Preflight

The WSL network path initially timed out during SSH banner exchange. The user
independently confirmed the server entrance from Windows. The diagnostic then used
Windows OpenSSH with strict host-key verification and the same dedicated audit key; it
did not use password authentication or relax host verification.

The authorized preflight revalidated the existing project-owned Worker 0. No vLLM
process was started, stopped, or restarted.

- SSH target: `root@121.48.161.243:4035`.
- Actual target: container `36f825bcc5da`, Ubuntu 22.04.3 LTS.
- GPU samples: `2026-11-24T07:17:06.509803157+00:00` and
  `2026-11-24T07:18:43.743335672+00:00`, separated by 97 seconds.
- Worker 0: container PID 382420, physical GPU 0,
  `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160`, remote port 8000.
- Worker 0 memory remained 21,781 MiB with 0% sampled utilization at both samples.
- State file, live cmdline, model path, port, CUDA mapping, GPU UUID, and authenticated
  `/v1/models` response agreed.
- Served model: `Qwen/Qwen3-8B`.
- Architecture/model type/dtype: `Qwen3ForCausalLM` / `qwen3` / `bfloat16`.
- vLLM `0.34.0`, Torch `2.14.0+cu132`, Transformers `5.22.2`.
- All seven frozen model config/index/shard SHA-256 values matched.
- Local and remote CA SHA-256 values matched. The server certificate retained
  `CN=localhost` with localhost DNS/IP SANs.
- TLS certificate and hostname validation remained enabled; no `verify=False` or
  `curl -k` was used.
- Worker 1 was read only to satisfy the existing remote-evidence schema; no Worker 1
  tunnel, API preflight, or model request occurred.
- Other GPU processes changed during preflight and were not touched.

## T1 Real-Qwen Execution

Fixed task: `p2_data_region_units_sum_001`; split: Train; Worker: `worker-0`.

1. Qwen returned a structured `read_csv` Tool Call.
2. The local read-only tool returned all six CSV rows.
3. Qwen received that actual Observation.
4. The second response returned a complete structured `filter_records` Tool Call whose
   six-row arguments exactly matched the Observation-derived frozen second step.
5. `filter_records` parsed and executed successfully in a separate ActionStep.
6. The Runner captured the Decision Checkpoint after the second Observation.
7. The Source next-model input was intercepted before Provider execution.
8. An independent environment was restored, six hashes were checked, and historical
   Tool replay was blocked.
9. The exact restored first input was sent once to real Qwen.
10. Qwen returned a structured `aggregate_records` Tool Call; the controlled boundary
    persisted it without parsing/executing the task tool, then stopped.

Scope controls:

| Control | Observed |
| --- | ---: |
| Source attempts | 1 |
| Source Provider requests | 2 |
| Restore Provider requests | 1 |
| Source local tool executions | 2 |
| Restore local tool executions | 0 |
| Automatic model retries | 0 |
| Replacement tasks | 0 |
| T2--T5 started | No |
| Full five-task Gate started | No |

## Provider Response and Parsing Evidence

| Field | Source 1 | Source 2 | Restore |
| --- | --- | --- | --- |
| Function | `read_csv` | `filter_records` | `aggregate_records` |
| Finish reason | `tool_calls` | `tool_calls` | `tool_calls` |
| Prompt tokens | 2,282 | 2,615 | 3,042 |
| Completion tokens | 31 | 292 | 123 |
| Total tokens | 2,313 | 2,907 | 3,165 |
| Delegated limit | 1,024 | 1,024 | 1,024 |
| Latency seconds | 1.594 | 7.368 | 3.832 |
| Structured calls | 1 | 1 | 1 |
| Parsing outcome | success | success | controlled `not_attempted` |

The Provider exposed response IDs for all three responses but no separate request ID;
unavailable request IDs remain `null`. Each Provider record has a unique local audit ID
and matching parsing record.

The critical second response:

- used 292/1,024 completion tokens;
- had `finish_reason=tool_calls`, not `length`;
- carried one structured `filter_records` call;
- contained 743 characters of function arguments;
- parsed successfully;
- response snapshot SHA-256:
  `18f79d5c0a46e3f92f4e0394713926c408641483afcdc7c82d65f63c2ac0178b`.

Usage observability was 3/3 responses, or **100%**. Aggregate usage was 7,939 prompt
tokens, 446 completion tokens, and 8,385 total tokens. Aggregate Provider latency was
12.794 seconds.

## Checkpoint and Independent Restore

Checkpoint:
`checkpoint_p2_data_region_units_sum_001_001`.

| Validation | Result |
| --- | --- |
| Capture step | 2 |
| Two successful dependent tool calls | PASS |
| Agent state hash | MATCH |
| Next model input hash | MATCH |
| Environment state hash | MATCH |
| Runtime state hash | MATCH |
| Identity config hash | MATCH |
| Combined state hash | MATCH |
| Exact Source/Checkpoint/Restore input | MATCH |
| Replay Guard | Blocked before execution |
| SQLite integrity | `ok` |
| SQLite independence | PASS |
| Checkpoint immutable after Restore | PASS |

The common next-input hash was:

`b7047cad0ef028d53c2e9a519abadd9fbedbd5f55cd3b87ebe10fffe337db014`.

The Checkpoint records `max_tokens=1024`, model calls used `2`, tool calls used `2`,
prompt tokens `4,897`, completion tokens `323`, and the cumulative 50,000-token budget.
Before/after the one Restore request, cumulative token usage was 5,220 / 8,385; counters
were not reset.

## Artifact Integrity

Authoritative local Run:

`runs/phase2_p2_d2_t1_budget_1024/p2_d2_t1_budget_1024_20261124_001/`

Key evidence includes:

- `run_manifest.json`
- `remote_preflight.json`
- `task_selection.json`
- `audit/provider_response_audit.jsonl`
- `audit/parsing_outcome_audit.jsonl`
- `tool_trajectories.jsonl`
- `decision_checkpoints.jsonl`
- `restore_audit.jsonl`
- `model_request_audit.jsonl`
- `budget_audit.jsonl`
- `checkpoint_inventory.jsonl`
- `replay_guard_audit.jsonl`
- `branch_isolation_audit.jsonl`
- `gate_summary.json`
- `diagnostic_post_run_analysis.json`
- `resource_release.json`
- `artifact_manifest.json`
- `artifact_manifest_post_audit.json`

The original 48-entry Manifest was independently re-hashed with zero mismatches. The
post-audit Manifest covers 50 artifacts, including the appended analysis and release
record. Original Run records and the original Manifest were not overwritten.

## Resource Release

- Existing remote processes started/stopped/restarted: 0 / 0 / 0.
- One Windows OpenSSH tunnel was created only for Worker 0 and owned by PID 91872.
- The exact owned PID/cmdline was verified before termination.
- Windows local port 8000 had no listener after cleanup.
- No Worker 1 tunnel was created.
- A temporary ACL-restricted Windows copy of the existing audit key was needed because
  Windows OpenSSH rejected the WSL UNC permission model. The copy and its dedicated
  temporary directory were deleted after the final remote check.
- The original WSL audit key was not modified.
- Final remote sample: `2026-11-24T07:21:02.913913817+00:00`.
- Worker 0 PID 382420 remained alive on the same GPU/UUID with stable 21,781 MiB memory.
- No other GPU process, service, historical Run, or 256-token configuration was
  modified.

## Local Verification

```text
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Results:

- full repository: 298 passed in 31.56 seconds;
- compileall: passed;
- diff check: passed.

## Interpretation and Remaining Limits

This Run provides direct evidence that the historical 256-token completion cap caused
the prior T1 truncation and that the separately versioned 1,024-token candidate was
sufficient for the same second Tool Call in this attempt. It also provides the first
real P2-D2 T1 Checkpoint/Restore success on the new P2-D1 catalog.

It does not prove:

- that 1,024 is the minimum or optimal cap;
- that all future Qwen Tool Calls will be structurally valid;
- that T2--T5 pass;
- that the complete five-task Gate passes;
- that the restored `aggregate_records` call would execute successfully;
- that this smoke result is eligible for model training or formal evaluation.

No model parameter, Prompt, Tool Schema, Observation, Catalog, verifier, Checkpoint
schema, Hash rule, Replay Guard, or 256-token historical configuration was changed in
Stage B.

## Acceptance Checklist

- [x] Exactly one fixed T1 Source attempt on project-owned Worker 0.
- [x] Separate 1,024-token Experiment/config identity used.
- [x] Second real dependent Tool Call completed and executed.
- [x] Provider Snapshot, finish reason, Usage, latency and parsing outcome complete.
- [x] Decision Checkpoint captured after the second Observation.
- [x] Independent Restore and all six hashes passed.
- [x] Exact first model input matched.
- [x] Replay Guard blocked historical execution.
- [x] One real Restore request completed without executing its Tool Call.
- [x] No retry, replacement, T2--T5, full Gate, or P2-D3.
- [x] Run remains non-formal and labels remain null.
- [x] Owned connection/credential resources released.
- [x] Full local regression passed.

## Git Commit

Only this sanitized report is eligible for Git delivery. Raw Runs, Provider snapshots,
remote evidence, keys, certificates, and temporary connection data remain excluded.
