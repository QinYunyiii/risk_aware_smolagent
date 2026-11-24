# P2-D2 T1 Real-Qwen Diagnostic Report

## Objectives

- Execute exactly one authorized real-Qwen Source attempt for the fixed T1 task.
- Persist the second Provider response before parsing and determine whether the frozen
  256-token completion limit caused the historical malformed Tool Call.
- Stop without retry, task replacement, T2--T5, or protocol changes.
- Release only connection resources created for this diagnostic.

## Status

**P2-D2 T1 Diagnostic: BLOCKED**

**Root cause: Completion Limit Truncation — CONFIRMED.**

The diagnosis is conclusive for this response because all three required signals were
observed together: `finish_reason=length`, `completion_tokens=256` equal to the actual
delegated limit of 256, and a visibly incomplete Tool Call JSON document. This finding
does not imply that every future parsing failure has the same cause and does not rule
out independent vLLM/smolagents parser compatibility issues in other responses.

The complete five-task P2-D2 Gate remains **BLOCKED**. This smoke diagnostic is not a
formal experiment and produced no training label.

## Git and Frozen Protocol

- Executed commit: `12a92d25011c66262203c605eae35e9a33517a85`.
- Branch: `main`; the worktree was clean before execution.
- Run ID: `p2_d2_t1_diagnostic_20261124_001`.
- Historical Run `p2_d2_real_qwen_20261124_001` was not modified.
- Model, Prompt, Tool Schema, Observation, reference steps, catalog, split, verifier,
  Checkpoint schema, hashes, replay guard, budgets, seed, and inference configuration
  were not changed.
- Frozen inference remained Qwen3-8B, seed `20261118`, temperature `0.0`,
  `enable_thinking=false`, 256 completion tokens, and 8,192 context tokens.

## Remote Preflight

The authorized preflight revalidated the existing project-owned services; no new vLLM
process was started.

- SSH target: `root@121.48.161.243:4035`, strict known-host verification and the
  dedicated audit key.
- Actual target: container `36f825bcc5da`, Ubuntu 22.04.3 LTS.
- GPU samples: `2026-11-24T06:12:09.801856201+00:00` and
  `2026-11-24T06:14:23.696778954+00:00`, separated by 134 seconds.
- Worker 0: container PID 382420, physical GPU 0,
  `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160`, port 8000.
- Worker 1: container PID 382422, physical GPU 1,
  `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d`, port 8001.
- State files, live cmdlines, model path, ports, CUDA mapping, GPU UUIDs, and
  authenticated `/v1/models` responses agreed.
- All seven frozen config/index/shard SHA-256 values matched.
- vLLM `0.34.0`, Torch `2.14.0+cu132`, and Transformers `5.22.2` matched.
- The local and remote CA SHA-256 matched. TLS certificate and hostname verification
  remained enabled; no `verify=False` or `curl -k` was used.
- Worker 1 received no diagnostic preflight or model request. The connection manager
  created a second idle ControlMaster, which was owned and released with Worker 0's
  tunnel after the Run.

## T1 Execution

Fixed task: `p2_data_region_units_sum_001`; fixed Worker: `worker-0`.

1. The first real Provider response returned a valid structured `read_csv` Tool Call.
2. The local read-only tool executed once and returned all six CSV records.
3. The second request included that Observation and asked Qwen to continue.
4. Qwen attempted `filter_records`, but the response reached the completion limit and
   ended during the sixth record's `units` field.
5. The Provider exposed no structured Tool Call for that choice, so smolagents invoked
   its text fallback parser.
6. The incomplete JSON raised `AgentParsingError`; the second tool did not execute.
7. The Runner stopped immediately.

Scope controls:

| Control | Observed |
| --- | ---: |
| T1 Source attempts | 1 |
| Provider requests | 2 |
| Successful local tool executions | 1 |
| Automatic model retries | 0 |
| Replacement tasks | 0 |
| T2--T5 started | No |
| Checkpoints created | 0 |
| Restore requests | 0 |

## Provider and Parsing Evidence

| Field | First response | Second response |
| --- | --- | --- |
| Audit request ID | `local-43bd5ced-8b59-4780-82ce-34557d4901b2` | `local-c22d6c87-fe14-4fb8-a48e-a410b25fd95d` |
| Provider response ID | `chatcmpl-b609bba7f82169d3` | `chatcmpl-8ca44c1c09781b83` |
| Provider request ID | Not exposed (`null`) | Not exposed (`null`) |
| Finish reason | `tool_calls` | `length` |
| Prompt tokens | 2,282 | 2,617 |
| Completion tokens | 31 | 256 |
| Total tokens | 2,313 | 2,873 |
| Delegated completion limit | 256 | 256 |
| Structured Tool Calls | One `read_csv` | `null` |
| Parsing | Success | `AgentParsingError` |

The second SDK-visible response snapshot was durably written before parsing. Its
SHA-256 is
`f022c7fa83387d8c4aa1e42fa5dcba52c13aec3dab1eb25c7e7d2cb68e6b8e26`.
The raw text ends inside the JSON string beginning with `"units`, before closing the
string, arguments object, Tool Call object, or wrapper. The parsing audit associates
the error with the exact second audit request ID and records category
`tool_call_parse_failure`.

Usage observability was 2/2 responses, or **100%**. Aggregate usage was 4,899 prompt
tokens, 287 completion tokens, and 5,186 total tokens. Aggregate Provider latency was
8.919 seconds.

## Checkpoint and Restore

Checkpoint eligibility requires two successful, sequential, observation-dependent
Source tool calls. The second Tool Call never parsed and therefore never executed.

- Checkpoint: not reached.
- Six-hash validation: not reached.
- Exact restored first input: not reached.
- Replay Guard: not reached.
- Real restored Qwen request: not reached.

The downstream audit streams explicitly record `not_reached`; they do not represent
these stages as failed comparisons or fabricated successes.

## Artifact Integrity

Authoritative local Run:

`runs/phase2_p2_d2_t1_diagnostic/p2_d2_t1_diagnostic_20261124_001/`

Important append-only evidence:

- `audit/provider_response_audit.jsonl`
- `audit/parsing_outcome_audit.jsonl`
- `tool_trajectories.jsonl`
- `gate_summary.json`
- `diagnostic_post_run_analysis.json`
- `resource_release.json`
- `artifact_manifest.json`
- `artifact_manifest_post_audit.json`

The original 17-entry manifest was independently re-hashed with zero mismatches. The
post-audit manifest covers 19 artifacts, including the appended diagnostic analysis and
resource-release record. Raw Run artifacts remain ignored and are not committed.

## Resource Release

- Existing vLLM processes started/stopped/restarted by this Run: 0 / 0 / 0.
- Owned local ControlMaster PIDs 823284 and 823305 were closed.
- Owned tunnel sockets and PID files were removed.
- Local ports 8000 and 8001 had no listeners after cleanup.
- Final remote sample: `2026-11-24T06:16:55.729619875+00:00`.
- Worker 0 PID 382420 and Worker 1 PID 382422 remained alive with the same GPU mapping
  and stable memory.
- No other GPU process, remote service, or historical Run was modified.

## Local Verification

```text
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Results:

- full suite: 282 passed in 29.66 seconds;
- compileall: passed;
- diff check: passed.

## Recommended Next Decision

The current evidence supports evaluating a controlled protocol change, but none was
made in this Run. Candidate remedies require explicit approval because they alter a
frozen research input:

1. raise the completion-token limit for a separately versioned diagnostic;
2. reduce Tool Call argument size by using a compact data handle or a narrower
   Observation/schema contract;
3. separately test vLLM structured Tool Call extraction and smolagents fallback parsing
   after the truncation issue is removed.

Do not rerun T1 with changed settings, restart vLLM, or enter P2-D3 without a new
approved protocol decision.

## Acceptance Checklist

- [x] Exactly one fixed T1 Source attempt executed on Worker 0.
- [x] No retry, replacement, or T2--T5 execution.
- [x] Provider response persisted before parsing.
- [x] Finish reason, Usage, delegated limit, response hash, and parsing outcome present.
- [x] Completion-limit truncation confirmed from direct evidence.
- [x] Usage completeness is 100%.
- [x] Checkpoint/Restore correctly marked not reached.
- [x] Owned connection resources released; existing Workers untouched.
- [x] Full local regression passed.
- [x] Frozen protocol unchanged.

## Git Commit

The report is the only repository-tracked Stage B change. Raw Run data, Provider
responses, remote evidence, keys, and certificates are excluded from Git delivery.
