# P2-D2 Audit Hardening Report

## Objectives

- Persist an SDK-visible Provider response snapshot, Provider Usage, finish reason,
  request identity and monotonic request latency before smolagents Tool Call parsing.
- Persist parsing success/failure and correlate it with the exact Provider request.
- Fail closed if required audit persistence fails, before a task tool can execute.
- Keep audit state outside Agent Memory, RuntimeState, Model Identity and Checkpoint v2.
- Use local synthetic and MockTransport tests only. Do not rerun P2-D2 or contact Qwen.

## Git Status

- Baseline HEAD: `1a08594e94ac9421f85a765fdf344f2029031ae5`.
- Final HEAD: `1a08594e94ac9421f85a765fdf344f2029031ae5` (unchanged; not committed).
- Branch: `main`.
- Baseline `origin/main`: `1a08594e94ac9421f85a765fdf344f2029031ae5`.
- Starting worktree: clean.
- Final worktree: contains only the P2-D2 Audit Hardening source, test and report changes.
- Commit: not created, as required by this task.
- Push: not performed, as required by this task.
- Historical Run `p2_d2_real_qwen_20261124_001`: not modified.

## Local Environment Work

The installed implementations were inspected directly:

- smolagents `1.26.0`;
- OpenAI SDK `3.52.0`;
- `ChatMessage.raw` from the installed `OpenAIModel`;
- OpenAI `ChatCompletion` Pydantic fields and `_request_id` populated from the
  response `x-request-id` header;
- `RecordingModel.generate()`, Agent parsing, JSONL fsync storage and P2-D2 Manifest
  generation.

The earliest non-invasive boundary available without changing site-packages or using a
global monkey patch is the returned `ChatMessage.raw`. It contains the response object
decoded by the OpenAI SDK. The implementation therefore calls it an
`SDK-visible Provider Response Snapshot`; it is **not** an HTTP wire-level raw body.

## Remote Environment Work

None. This task used no SSH, remote HTTPS API, Qwen model, GPU, vLLM or Docker.

## Work Completed

### Independent Provider audit

`provider_response_audit.py` adds an append-only audit subsystem with two files:

```text
audit/provider_response_audit.jsonl
audit/parsing_outcome_audit.jsonl
```

Each file uses UTF-8, one event per line, mode `0600`, a process-local thread lock,
append writes and `fsync`. The directory uses mode `0700`.

Provider events include:

- locally generated `audit_request_id` and independently observed
  `provider_request_id`;
- Run, task, attempt, phase, Worker, model and sanitized endpoint identity;
- canonical existing model-input hash and a generation-configuration hash;
- SDK-visible response snapshot and SHA-256 of its sanitized canonical form;
- Provider response ID, served model, choice index, message content, structured Tool
  Calls, Tool Call IDs, function names and arguments;
- finish reason, Provider Usage, ChatMessage Usage and their consistency result;
- the actual delegated Completion Token limit used for finish-reason comparison;
- request start timestamp, response timestamp and monotonic request latency;
- transport/provider exception type, sanitized message and null unavailable fields.

Missing Usage and finish reason remain `null`. No missing value is converted to zero,
and total tokens are not inferred when the Provider omits them.

### RecordingModel boundary

`RecordingModel.generate()` now creates a unique audit request ID before provider
delegation. On response, it synchronously persists the audit event before returning the
`ChatMessage` to the Agent. On provider exception, it persists a failure event with
observed latency and null response fields.

If either event cannot be persisted, `AuditPersistenceError` is raised, the request is
recorded in memory with `status=audit_persistence_failure`, and Agent execution stops
before any response Tool Call can run.

### Parsing outcome boundary

The request ID is attached to the exact in-memory `ChatMessage` object only after the
Provider event has been durably written. It is not serialized by the Agent state
adapter. `CheckpointableToolCallingAgent` then records:

- `success` immediately before yielding the first parsed ToolCall;
- `failed` with `tool_call_parse_failure`, exception type, sanitized message and
  traceback when an actual `AgentParsingError` occurs;
- `not_attempted` for the controlled restore response, which intentionally stops before
  parsing.

The original exception is re-raised. No retry, fake success or altered tool behavior was
introduced. Correlation does not use a global `latest_request`; it follows the exact
message object and remains isolated under concurrent Worker activity.

### P2-D2 integration and Artifact Manifest

`P2D2RealQwenGateRunner` creates one audit subsystem per new Run and supplies immutable
Source/Restore request contexts. Its existing recursive Artifact Manifest logic includes
both `audit/` JSONL files with relative path, byte size and SHA-256, including after an
exception through the entrypoint's existing `finally` block.

The historical Run and historical Manifest were not regenerated or changed.

## Files Changed

New:

- `src/risk_agent/monitoring/provider_response_audit.py`
- `tests/agent/test_provider_response_audit.py`
- `docs/phase2/daily_reports/p2_d2_audit_hardening_report.md`

Modified:

- `src/risk_agent/monitoring/__init__.py`
- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/collection/p2_d2_real_qwen_gate.py`
- `tests/test_p2_d2_real_qwen_gate.py`

No Benchmark catalog, task, Prompt, Tool Schema, model configuration, retry policy,
Checkpoint schema, capture/restore logic or Hash implementation changed.

## Provider Audit Results

The P2-D2 SDK/MockTransport integration generated three real OpenAI SDK
`ChatCompletion` objects:

| Field | Observable | Result |
| --- | --- | --- |
| Local audit request ID | Yes | 3/3 unique |
| Provider request ID | When Provider/header supplies it | 3/3 captured |
| Worker/model identity | Yes | 3/3 captured |
| Input hash | Yes | 3/3 captured |
| SDK response snapshot | Yes | 3/3 captured |
| Structured Tool Calls | Yes | 3/3 captured |
| Finish reason | Yes in this fixture | 3/3 captured |
| Prompt/Completion/Total Usage | Yes in this fixture | 3/3 captured |
| Monotonic latency | Yes | 3/3 captured |
| Sanitized snapshot SHA-256 | Yes | 3/3 verified |
| HTTP wire raw body | No | NOT OBSERVABLE by this boundary |

Provider request IDs remain `null` when neither the SDK response nor exception exposes
one. This local ID is never replaced with the local audit ID.

## Parsing Audit Results

- Normal structured Tool Call: persisted and correlated before one local tool execution.
- Complete textual Tool Call: original content persisted; parsing success correlated.
- Malformed textual Tool Call: response persisted first; `AgentParsingError`, traceback
  and failure category persisted; tool execution count remained zero; original exception
  propagated.
- Synthetic `finish_reason=length`, `completion_tokens=256`: preserved as a received
  Provider response plus parsing failure, not classified as transport failure. This test
  is synthetic evidence only and does not prove the cause of the historical Qwen failure.
- Controlled restore response: explicitly recorded as `not_attempted` rather than a fake
  parsing success.

## Usage Completeness

For the installed-SDK P2-D2 MockTransport integration sample (`n=3`):

- complete Provider Usage: `3/3 = 100%`;
- Prompt Tokens missing: `0/3 = 0%`;
- Completion Tokens missing: `0/3 = 0%`;
- Total Tokens missing: `0/3 = 0%`;
- finish reason missing: `0/3 = 0%`.

Separate negative tests intentionally supplied missing and partial Usage and a missing
finish reason. They verified null preservation and `usage_completeness=incomplete`; they
are not included in the complete-response denominator above. A mismatch test verified
that Provider and normalized ChatMessage Usage are both retained with an explicit
`mismatch` result.

Real-Qwen Usage completeness remains **NOT VERIFIED** because this task was prohibited
from contacting the remote model.

## Checkpoint Isolation

- Agent state schema remains `2.0.0`.
- The same Agent snapshot captured before and after an external audit append was byte-for-
  byte equivalent as a serialized document, including `agent_state_hash` and
  `next_model_input_hash`.
- RuntimeState remained exactly equal.
- Model fingerprint remained exactly equal.
- Audit request IDs were absent from the Agent snapshot.
- Existing Checkpoint v2, isolation and model-backed checkpoint regression tests passed.
- The full six-Hash implementation was not modified. Existing six-Hash restore tests and
  the P2-D2 SDK integration passed unchanged.

## Security and Concurrency

- Recursive key and text redaction covers Bearer tokens, API keys, passwords,
  Authorization fields, private keys, access tokens and common secret fields.
- Endpoint credentials and query strings are removed.
- Snapshot Hash is explicitly the hash of the sanitized SDK snapshot, not of an original
  HTTP response body.
- A 40-request, two-Worker concurrent test produced 40 intact Provider lines and 40
  correctly correlated parsing lines, with unique request IDs and no Worker cross-talk.
- A synthetic persistence failure stopped before tool execution and retained the distinct
  `audit_persistence_failure` classification.

## Commands and Tests

```text
.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_provider_response_audit.py
.venv/bin/python -m pytest -q --disable-warnings tests/test_p2_d2_real_qwen_gate.py tests/agent/test_checkpointable_agent.py tests/agent/test_provider_response_audit.py
.venv/bin/python -m pytest -q --disable-warnings tests/agent/
.venv/bin/python -m pytest -q --disable-warnings tests/test_checkpoint.py tests/test_checkpoint_v2.py tests/test_checkpoint_v2_isolation.py tests/test_model_backed_checkpoint.py tests/agent/test_day5_real_wiring.py tests/agent/test_b3_continue_gate.py tests/agent/test_b4_smoke_gate.py tests/test_p2_d1_catalog.py tests/test_p2_d2_real_qwen_gate.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

## Test Results

- New audit test file: 13 test functions / 16 pytest cases passed.
- Strengthened P2-D2 installed-SDK MockTransport integration passed.
- Agent test group: 123 passed.
- Checkpoint, B2, B3, B4, P2-D1 and P2-D2 grouped regression: 78 passed.
- Final Stage A repository regression after adding the fixed T1 diagnostic entry:
  282 passed, 0 failed, 0 skipped.
- `compileall`: passed.
- `git diff --check`: passed.

## Acceptance Checklist

- [x] SDK-visible response persisted before Tool Call parsing.
- [x] Request/Worker/model/input/config identities persisted without credentials.
- [x] Text content and structured Tool Calls both retained.
- [x] Finish reason, three Usage fields, latency and sanitized response Hash retained.
- [x] Missing/partial fields remain null.
- [x] Provider/ChatMessage Usage consistency checked.
- [x] Provider exceptions recorded without retry or fabricated Usage.
- [x] Parsing success/failure/controlled-not-attempted correlated exactly.
- [x] Audit persistence fails closed before tool execution.
- [x] JSONL append, flush/fsync, permissions and concurrency tested.
- [x] Audit state excluded from Checkpoint authoritative state and identities.
- [x] New audit files covered by the new-Run Artifact Manifest.
- [x] Historical Run and Manifest unchanged.
- [x] Full regression passed.

## Resource Audit

| Operation | Count |
| --- | ---: |
| Real Qwen requests | 0 |
| SSH operations | 0 |
| GPU queries/use | 0 |
| vLLM start/stop/restart | 0 |
| Docker operations | 0 |
| Remote API requests | 0 |
| Formal experiment Runs | 0 |

All Provider behavior in this task came from local FakeModel or HTTP MockTransport
fixtures under pytest temporary directories.

## Known Issues

- The SDK-visible snapshot is not a wire-level HTTP body. Wire raw response capture is
  NOT OBSERVABLE at the selected non-invasive boundary and was not fabricated.
- Real Qwen/vLLM field availability and real-Run Usage completeness are NOT VERIFIED.
- The historical 256-token failure cause remains unconfirmed; this change only makes a
  future authorized Run diagnosable.

## Remaining Work

- User review of the uncommitted local diff.
- A future P2-D2 diagnostic/real Gate requires separate explicit remote authorization.
- Do not rerun T1–T5 or start P2-D3 under this task.

## Status: PASS

`P2-D2 Audit Hardening = PASS`

All local implementation, fail-closed persistence, parsing correlation, security,
concurrency, Checkpoint isolation and full regression criteria passed without changing
the frozen experiment protocol. Real-Qwen behavior remains explicitly NOT VERIFIED.

## Git Commit

Not created. The task explicitly requires retaining local changes for user review and
forbids automatic Commit and Push.
