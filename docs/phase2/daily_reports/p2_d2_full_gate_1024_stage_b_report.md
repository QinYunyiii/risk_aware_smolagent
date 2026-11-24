# P2-D2 Full Five-Task Gate (1024-token) — Stage B Report

## Objectives

- Execute the independently authorized real-Qwen five-task Gate from frozen Git
  commit `aaf93c2adbf6d081c97127176c980cc2d7a174d2`.
- Use only the two revalidated existing project Workers and the fixed T1--T5 order.
- Allow one Source attempt per task, stop at the first failure, and perform no retry,
  replacement, skip, or Worker failover.
- Preserve Provider Audit, Checkpoint v2, independent Restore, six hashes, exact input
  matching, Replay Guard, raw evidence, and connection-resource accounting.

## Local Environment Work

- Verified a clean worktree with local HEAD and `origin/main` both at the authorized
  commit before execution.
- Confirmed approximately 942 GiB of free local storage.
- Loaded the frozen `p2-d2-full-gate-1024-v1` experiment configuration and reused the
  verified `qwen3-8b-completion-1024-v1` model configuration.
- Created the unique Run ID `p2_d2_full_gate_1024_20261124_001`.
- Executed local tools, Checkpoint/Restore, Provider Audit, and Gate control from WSL.
- No Prompt, Tool Schema, Catalog, task definition, model parameter, budget,
  Checkpoint schema, Hash rule, or historical Run was modified.

## Remote Environment Work

All remote operations were read-only. No vLLM process, model, TLS configuration,
remote directory, package, or GPU allocation was started, stopped, restarted, or
modified.

- Strict known-host verification and the dedicated audit key reached the expected
  Ubuntu 22.04 container through the configured laboratory SSH endpoint.
- GPU samples at `07:57:41` and `07:59:05` UTC were separated by 83 seconds.
- Existing Worker state files, container PIDs, cmdlines, loopback ports, model path,
  `CUDA_VISIBLE_DEVICES`, GPU UUID, and stable memory usage agreed.

| Worker | Container PID | Physical GPU | Memory | Local verified endpoint |
| --- | ---: | ---: | ---: | --- |
| worker-0 | 382420 | 0 | 21,781 MiB | `https://localhost:8000/v1` |
| worker-1 | 382422 | 1 | 21,779 MiB | `https://localhost:8001/v1` |

- Other GPUs changed ownership/usage between samples and were not touched.
- Model identity remained `Qwen/Qwen3-8B`, `Qwen3ForCausalLM`, `qwen3`, bfloat16.
- vLLM `0.34.0`, Torch `2.14.0+cu132`, and Transformers `5.22.2` matched the frozen
  identity.
- `config.json`, the safetensors index, and all five model shards matched all seven
  frozen SHA-256 values.
- Local and remote CA SHA-256 matched. The certificate remained valid for `localhost`
  with strict hostname verification.
- Both authenticated `/v1/models` checks returned the expected model; unauthenticated
  checks returned HTTP 401.
- Two run-owned local forwards were created for ports 8000/8001. A separate owned SSH
  connection was used only for read-only evidence collection.

## Work Completed

### T1 — PASS

Task `p2_data_region_units_sum_001` ran on worker-0.

1. Qwen returned `read_csv`; the local tool executed successfully.
2. Qwen returned the Observation-dependent `filter_records`; it parsed and executed
   successfully in a separate ActionStep.
3. Checkpoint `checkpoint_p2_data_region_units_sum_001_001` was captured after the
   second Observation.
4. Independent Restore passed SQLite/workspace isolation and all six hashes.
5. Source, Checkpoint, and Restore next-model input matched exactly.
6. Replay Guard blocked the completed call before execution without changing state or
   budget.
7. The one controlled Restore request returned `aggregate_records`; the tool was not
   executed.

| Request | Tool | Prompt tokens | Completion tokens | Latency seconds |
| --- | --- | ---: | ---: | ---: |
| Source 1 | `read_csv` | 2,282 | 31 | 5.930 |
| Source 2 | `filter_records` | 2,613 | 292 | 9.523 |
| Restore | `aggregate_records` | 3,039 | 123 | 6.759 |

T1 totals were 7,934 prompt tokens, 446 completion tokens, 8,380 total tokens, and
22.211 seconds of Provider latency. The delegated completion limit was 1,024 for all
three requests.

### T2 — INFRASTRUCTURE FAILURE

Task `p2_file_report_control_001` ran on worker-1.

1. Qwen returned `list_files`; the local tool executed successfully.
2. Qwen returned the Observation-dependent `search_paths`; it parsed and executed
   successfully in a separate ActionStep.
3. Checkpoint `checkpoint_p2_file_report_control_001_002` was captured after the
   second Observation.
4. The independent restored workspace was present and equal to Source state.
5. Offline `verify_v2` passed all checkpoint integrity checks. The attempted Restore
   input hash matched the Checkpoint next-input hash.
6. The controlled Restore Provider request received no response and failed after
   64.184 seconds with `APIConnectionError` / `transport_failure`.

| Request | Tool/outcome | Prompt tokens | Completion tokens | Latency seconds |
| --- | --- | ---: | ---: | ---: |
| Source 1 | `list_files` | 1,617 | 29 | 3.942 |
| Source 2 | `search_paths` | 1,741 | 78 | 3.566 |
| Restore | transport failure | null | null | 64.184 |

The restored request is reached only after the Runner's restore-hash and replay checks.
However, because the Provider request failed, the Runner correctly did not create a T2
success record or count T2 as a successful Restore. No request was retried.

### Stop behavior

- Gate result: `BLOCKED`.
- Completed tasks: 1/5.
- Attempted tasks: T1 and T2 only.
- T3, T4, and T5 were never started; therefore the real T3 idempotency check was not
  reached.
- Automatic model retries: 0.
- Replacement tasks: 0.
- Worker failovers: 0.
- Existing successful T1 artifacts were preserved.

## Provider Audit

- Provider records: 6.
- Responses received: 5; transport failures: 1.
- Parsing records: 5; four successful Source parses and one controlled T1 Restore
  `not_attempted` outcome.
- Every received response preserved the raw Provider snapshot, response hash, input
  hash, finish reason, Usage, latency, served model, Worker, task, and 1,024-token
  delegated limit.
- The failed request preserved its phase, task, Worker, input/config hashes, latency,
  exception category, and null Usage rather than fabricating zeroes.
- Usage completeness was 5/5 for received responses and 5/6 (83.3%) across all
  attempted requests. The full Gate therefore did not satisfy the success threshold.

## Checkpoint and Restore Evidence

| Validation | T1 | T2 |
| --- | --- | --- |
| Two dependent Source tool calls | PASS | PASS |
| Checkpoint Schema 2.0.0 | PASS | PASS |
| Offline `verify_v2` | PASS | PASS |
| Source/Restore environment equality | PASS | PASS |
| Checkpoint/Restore request input hash | MATCH | MATCH |
| Six hashes counted as completed Gate result | PASS | Not counted after Provider failure |
| Controlled Restore response received | PASS | FAIL |
| Replay audit persisted | PASS | Not persisted after Provider failure |

T2's partial engineering evidence is retained for forensics only. It is not promoted to
a successful task, a Gate PASS, or a research label.

## Artifacts Generated

Authoritative local Run:

`runs/phase2_p2_d2_full_gate_1024/p2_d2_full_gate_1024_20261124_001/`

The raw Run includes its manifest, task selection, remote preflight, Provider/parsing
audit, tool trajectories, both Checkpoints, environments, Gate summary, failure record,
post-run analysis, and resource-release record.

- Original artifact Manifest: 87 entries, 0 SHA-256 mismatches.
- Append-only post-audit Manifest: 89 entries, 0 SHA-256 mismatches.
- The original Manifest and all original Run artifacts were not overwritten.
- Raw Runs, Provider snapshots, remote evidence, keys, certificates, and connection
  details remain local and are excluded from Git.

## Resource Release

- At the first post-run check, all three run-owned Control Sockets were already absent
  and local ports 8000/8001 had no listeners. This agrees with the T2 transport failure.
- The owned temporary connection directory was empty and removed.
- No temporary key copy was created; the original audit key was not modified.
- A fresh read-only final SSH check at `14:40:33` UTC confirmed both pre-existing Worker
  PIDs alive with the same GPU mapping and stable 21,781/21,779 MiB memory.
- Remote processes started/stopped/restarted: 0/0/0.
- Other GPU processes modified: 0.

## Commands and Tests

```text
.venv/bin/python scripts/run_p2_d2_full_gate_1024.py \
  --mode full-five-task-gate-1024 \
  --authorize-remote \
  --run-id p2_d2_full_gate_1024_20261124_001 \
  --remote-evidence <local-ignored-evidence.json>

.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

## Test Results

- Full local regression: `308 passed in 34.46s`.
- Compile check: PASS.
- Artifact verification: PASS for both original and post-audit Manifests.
- No test was skipped or weakened.

## Acceptance Checklist

- [x] Authorized frozen commit/configuration used.
- [x] Existing project-owned Workers and GPU mappings freshly verified.
- [x] Model, weight, TLS, API authentication, and endpoint identity verified.
- [x] Unique Run ID and fixed T1--T5 ordering used.
- [x] T1 completed Provider Audit, Checkpoint, independent Restore, six hashes, exact
      input match, Replay Guard, and one controlled Restore request.
- [x] T2 completed its single Source attempt and preserved partial Checkpoint evidence.
- [x] T2 infrastructure failure was accurately recorded with null Usage.
- [x] Gate stopped immediately; T3--T5 were not started.
- [x] No retry, replacement, failover, vLLM operation, or GPU reassignment occurred.
- [x] Original and appended artifact integrity verified.
- [x] Owned local connection resources released and remote Workers retained.
- [x] Research flags remained non-formal and all intervention labels remained null.
- [ ] Five real tasks completed.
- [ ] Usage completeness reached the successful-Gate threshold.
- [ ] Full five-task Gate passed.

## Known Issues

- The two run-owned forwarding ControlMasters disappeared before T2's Restore request
  completed. The existing remote Worker remained alive, so the observed failure is a
  connection infrastructure failure rather than a model or task failure.
- This frozen Run cannot be resumed, retried, or converted into PASS.
- Real T3 idempotency and T3--T5 Checkpoint/Restore remain unexecuted.

## Remaining Work

- Keep this Run immutable as the authoritative failed attempt.
- Any new five-task attempt requires separate user authorization, a new unique Run ID,
  fresh remote evidence, and a reviewed connection-stability plan. It must not reuse or
  overwrite this Run.
- Do not start P2-D3 or formal data collection from this result.

## Status: BLOCKED

T1 passed. T2 reached its controlled Restore request but failed at the transport layer.
The Runner stopped exactly as required, so the real five-task Gate remains `BLOCKED`.

## Git Commit

- Executed code commit: `aaf93c2adbf6d081c97127176c980cc2d7a174d2`.
- Only this sanitized report is eligible for Git delivery; the report commit is recorded
  in repository history after local verification.
