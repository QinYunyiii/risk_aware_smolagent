# B2 Day 5 Stage A Report

## Objectives

- Audit the frozen Protocol v2 and current B2 checkpoint implementation from baseline commit
  `467548ee5b997fa57a9ddd9ae5d2b69c99008da9`.
- Add the minimum production wiring from `OpenAIServerModel` through `RecordingModel` and
  `CheckpointableToolCallingAgent` to the real local benchmark tools.
- Add a controlled restored-request boundary that delegates exactly one model request and stops
  before parsing or executing any returned task Tool Call.
- Provide an offline-default Day 5 runner and prove it with ScriptedModel and real OpenAI SDK
  `MockTransport` tests.
- Stop before every remote, GPU, vLLM, SSH and real Qwen operation.

## Local Environment Work

- Date: 2026-11-23 (Asia/Shanghai).
- Branch: `master`.
- Baseline/current HEAD: `467548ee5b997fa57a9ddd9ae5d2b69c99008da9`.
- `origin/main`: `467548ee5b997fa57a9ddd9ae5d2b69c99008da9`.
- Initial worktree: clean.
- Baseline regression: 222 passed.
- Python entry point: `.venv/bin/python`.
- Installed versions recorded by local preflight: smolagents 1.26.0, OpenAI SDK 3.52.0,
  httpx2 2.16.2.

The required repository, protocol, schema, acceptance, budget, execution-design and latest
hardening documents were reviewed before implementation. The frozen Protocol v2 and B2–B4
acceptance protocol were treated as authoritative over historical day-number plans.

## Remote Environment Work

None.

- SSH sessions: 0.
- Real network requests: 0.
- Real Qwen requests: 0.
- GPU inspections or allocations: 0.
- vLLM processes started, stopped or inspected: 0.
- HTTPS endpoint probes: 0.
- Tunnel changes: 0.

`configs/remote_model.yaml` currently declares two HTTPS localhost endpoints, but Stage A did not
interpret them as physical remote hosts or prove that a tunnel, certificate, authorized service,
model snapshot or Worker-to-GPU mapping exists.

## Work Completed

### Production Agent wiring

The shared provider factory now creates one TLS-configured, no-retry `OpenAIServerModel` and
attaches the selected Worker ID, endpoint and configured model-version declaration as secret-free
fingerprint material. The new checkpointable factory returns:

```text
OpenAIServerModel
  -> RecordingModel(RuntimeState, max_model_calls=10)
  -> CheckpointableToolCallingAgent(max_steps=10)
  -> LocalBenchmarkTool instances
```

The pre-existing ordinary calculator and Day 8 factories use the same provider constructor and
retain their public return types and behavior. OpenAI Client `max_retries=0` and smolagents
`retry=False` remain unchanged.

### Controlled restored request

`CheckpointableToolCallingAgent.request_next_model_response()` validates restored task/memory,
live RuntimeState, remaining step budget and the unconsumed first-request contract. It advances to
the frozen next step, delegates exactly one request through `RecordingModel`, returns the provider
response for evidence, and deliberately does not parse Tool Calls, invoke `process_tool_calls`, or
append a partially executed ActionStep.

The contract becomes consumed after the request, so the method cannot be called a second time on
the same restored Agent.

### Local tool adapter state

The previous opaque `itertools.count` cursor in each `LocalBenchmarkTool` was replaced by an
explicit integer cursor. Capture and restore helpers now persist the future tool-log Step ID for
all ordered local tools. A failed tool attempt still advances the cursor and remains traceable.

### Real-provider response compatibility

The OpenAI SDK-backed `ChatMessage` returned by the installed smolagents version carries its role
as a plain string, while framework-created memory messages use `MessageRole`. Canonical Agent
serialization now accepts both forms and writes the same role string. This was found by the new
production-path MockTransport test before Checkpoint capture.

### Day 5 runner

`scripts/run_b2_day5_real_qwen_gate.py` supports:

- `local-preflight` (default and network-free);
- `remote-preflight`;
- `single-checkpoint`;
- `five-checkpoint-gate`.

Every remote mode fails before output or network access unless `--authorize-remote` is explicitly
provided. Single/five execution additionally requires an allowlisted remote-evidence JSON object
confirming remote authorization, HTTPS service authorization, model-version verification and
physical GPU mapping verification. Arbitrary evidence fields are rejected to reduce the chance of
persisting secrets.

Run directories are always created with `exist_ok=False`. The runner uses a deterministic
Train-only candidate order covering all four domains before the fifth candidate, fixed Worker
selection per attempt, exact Protocol v2 budgets, CheckpointManager v2, independent restore
workspaces, source-generator closure checks and immutable checkpoint inventories.

The runner persists all three canonical first-input representations (source boundary, checkpoint
authority and restored actual boundary), all six checkpoint hashes, provider response and Usage,
budget counters, tool-history delta and non-formal research flags. It never runs the verifier and
sets `y_continue` to null.

## Files Changed

- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/agent/agent_state_adapter.py`
- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/agent/__init__.py`
- `src/risk_agent/collection/benchmark_tools.py`
- `src/risk_agent/collection/day5_gate.py`
- `src/risk_agent/collection/__init__.py`
- `scripts/run_b2_day5_real_qwen_gate.py`
- `tests/agent/test_day5_real_wiring.py`
- `docs/phase2/daily_reports/b2_day5_stage_a_report.md`

No frozen protocol, Checkpoint schema, model parameter, budget value, task definition, dataset
split, verifier rule or local/remote responsibility boundary was changed.

## Artifacts Generated

Local-only preflight artifact (ignored run storage, not formal data):

`runs/phase2_b2_day5/b2_day5_stage_a_20261123_002/`

- `run_manifest.json`
- `local_preflight.json`

The preflight selected 24 deterministic Train candidates and fixed the first five candidate IDs.
It recorded `network_requests_made: 0`, all frozen-config checks true, more than 20 GiB local free
space, `formal_experiment: false`, `research_dataset_eligible: false`, and `y_continue: null`.
The earlier local-only `_001` preflight artifact was retained rather than overwritten or deleted.

## Commands and Tests

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q tests/agent/test_day5_real_wiring.py --disable-warnings` | 6 passed |
| `.venv/bin/python -m pytest -q --disable-warnings` | 228 passed in 13.32s |
| `.venv/bin/python -m compileall -q src tests` | exit 0, no output |
| `git diff --check` | exit 0, no output |
| `scripts/run_b2_day5_real_qwen_gate.py --mode local-preflight ...` | PASS, 0 network requests |

The suite increased from 222 to 228 tests. No test was skipped.

## Test Results

The new integration test uses the actual production factory, installed `OpenAIServerModel`, OpenAI
SDK and `httpx2.MockTransport`. Observed behavior:

- source request count: 1;
- source local tool executions: 1;
- source next-input validation: intercepted before Provider delegation;
- Checkpoint v2 capture and complete verification: PASS;
- independent restore and all six hash comparisons: PASS;
- source/checkpoint/restored canonical bytes and hashes: exact match;
- restored controlled Provider requests: 1;
- restored response Usage: preserved from Provider response;
- restored response contained a new calculator Tool Call;
- restored Tool History additions: 0;
- restored environment mutation: 0;
- original checkpoint inventory changes: 0;
- HTTP 429 transport attempts through the new factory: exactly 1;
- repeated controlled-request attempt: rejected locally.

ScriptedModel separately proves the controlled response is one-shot, creates no new memory step,
and never processes the returned FinalAnswer Tool Call. CLI tests prove the default preflight is
offline, existing output cannot be overwritten, non-formal flags are preserved, and a remote mode
without explicit authorization creates no output directory.

## Acceptance Checklist

- [x] Repository and frozen protocol audited from the confirmed baseline.
- [x] Historical `run_day5_checkpoint.py` kept distinct from the real-Qwen Day 5 entry.
- [x] Production provider/recorder/checkpointable-Agent/local-tool wiring added.
- [x] TLS/no-retry/Worker identity settings retained and fingerprinted.
- [x] Checkpoint v2 capture, verification and independent restore covered offline.
- [x] Source generator closes without a new Provider call or tool replay.
- [x] Runtime Model/Token/Tool/latency budgets continue across restore.
- [x] First restored input is checked at the actual RecordingModel boundary.
- [x] Controlled response stops before every new task-tool side effect.
- [x] HTTP 429 reaches the transport exactly once.
- [x] Run output refuses overwrite and records non-formal/null-label flags.
- [x] Full local regression and compile checks pass.
- [ ] Remote host/container identity verified (requires authorization).
- [ ] Existing authorized HTTPS/vLLM service verified (requires authorization).
- [ ] Physical GPU IDs and Worker mapping verified twice (requires authorization).
- [ ] Five real Qwen source/restore pairs executed (requires authorization).

## Known Issues

- The configured `model_version` is only a declaration until remote weights and launch arguments
  are independently verified.
- The configured `https://localhost:8000/v1` and `:8001/v1` endpoints are unproven until the TLS
  chain, hostname/tunnel interpretation, authentication and ownership are checked.
- Exact pre-request Qwen context-token counting is not exposed by the current client boundary.
  The run manifest records this as unavailable and confirms no silent truncation, summarization or
  compression is enabled; Provider Usage remains authoritative after a request.
- The remote SSH identity, remote writable directories, vLLM environment and current GPU/resource
  ownership are unknown because Stage B was intentionally not entered.

## Remaining Work

After separate user authorization:

1. Verify the configured SSH target/container identity without guessing connection details.
2. Perform two independent shared-GPU occupancy checks and obtain laboratory approval for at most
   two currently unused physical RTX 4090 devices.
3. Verify or, only if necessary and approved, start two owned vLLM workers; record physical GPU
   IDs, Worker mapping, PIDs, launch arguments, actual weight identity and vLLM version.
4. Verify both HTTPS endpoints, CA/hostname validation, API authentication, `/v1/models`, no-retry
   settings and service ownership without `verify=False` or `curl -k`.
5. Run the five-checkpoint gate sequentially: the first task must pass before the remaining four.
6. Preserve every failed attempt and all evidence, release only resources created by this run, then
   generate `b2_day5_real_qwen_report.md` with the actual final Gate status.

## Status: PASS

Stage A local engineering and offline acceptance are PASS. The B2 Day 5 real-Qwen Gate has not
been run and has no PASS status; it remains pending the required remote authorization Gate. B3 and
formal collection have not started and are not authorized.

## Git Commit

- Commit created: no.
- Push performed: no.
- Current HEAD remains `467548ee5b997fa57a9ddd9ae5d2b69c99008da9`.
