# P2-D2 Audit Hardening Delivery and T1 Diagnostic Preparation Report

## Objectives

- Re-audit and deliver the completed P2-D2 Provider/Parsing Audit Hardening.
- Add a strictly fixed, one-attempt T1 diagnostic entry without changing the five-task
  Gate.
- Verify the diagnostic success and malformed-second-response paths entirely with local
  OpenAI SDK/MockTransport fixtures.
- Commit and push the Stage A delivery only. Do not execute Stage B.

## Git Status

- Baseline local HEAD: `1a08594e94ac9421f85a765fdf344f2029031ae5`.
- Baseline `origin/main`: `1a08594e94ac9421f85a765fdf344f2029031ae5`.
- Branch: `main`.
- Starting changes: the previously completed, uncommitted Audit Hardening only.
- No unrelated user modification was found.
- Delivery commit message:
  `feat: harden provider auditing and prepare P2-D2 T1 diagnostic`.
- The exact containing Commit SHA and Push result are reported in the final Stage A
  handoff because a Git commit cannot contain its own SHA.

## Audit Hardening Review

The existing implementation passed re-review without requiring a redesign:

- the SDK-visible Provider response is durably appended and fsynced before the
  `ChatMessage` returns to Agent parsing;
- it is accurately named an SDK-visible snapshot, never an HTTP wire raw response;
- raw text content and structured Tool Calls are both retained;
- Provider request IDs are read only when actually exposed by the SDK/response headers,
  and remain null otherwise;
- finish reason is not inferred;
- Prompt, Completion and Total Usage remain independently nullable;
- Provider and ChatMessage Usage are retained and consistency checked;
- parsing outcomes are linked by an audit ID carried on the exact in-memory response
  object, not a global latest-request variable;
- `AuditPersistenceError` stops execution before any response Tool Call can execute;
- sensitive keys, Bearer values, endpoints and exception text are sanitized;
- audit state remains outside Agent Snapshot, RuntimeState, Model Identity and all six
  Checkpoint hashes;
- no retry or extra model call was added.

The audit now also records `delegated_completion_token_limit`. This lets a future T1
diagnostic compare `completion_tokens` with the actual application-side delegated limit
instead of assuming that the configured value reached the request unchanged.

## Fixed T1 Diagnostic Entry

New mode:

```text
--mode single-t1-diagnostic
```

Its invariants are enforced in both CLI and Runner code:

- task is always `p2_data_region_units_sum_001`;
- there is no `--task-id` option;
- Worker is always `worker-0`;
- exactly one task attempt is allowed;
- automatic model retries and task replacement are zero;
- T2–T5 are never started, on either T1 success or failure;
- the default output root is
  `runs/phase2_p2_d2_t1_diagnostic/<unique_run_id>/`;
- only Worker 0 receives API/TLS preflight in this mode;
- the existing `gate` path still requires two Workers and retains its fixed five-task
  behavior;
- a successful second dependent Tool Call continues through the existing unmodified
  Checkpoint/Restore path and then stops;
- a parsing failure records all available evidence and stops before Checkpoint/Restore.

The CLI continues to require both `--authorize-remote` and remote evidence for this
remote mode. Stage A invoked neither.

## Local Diagnostic Tests

### Successful T1

A local OpenAI SDK/MockTransport sequence returned:

1. structured `read_csv`;
2. structured `filter_records` whose `records` were the actual first Observation;
3. one controlled restored response.

Results:

- attempted Task IDs: T1 only;
- Provider requests: 3;
- dependent Source tool calls: 2;
- eligible Checkpoint: 1;
- six hashes: all matched through the existing assertions;
- exact Source/Checkpoint/Restore input: matched;
- replay guard: passed;
- real restore boundary in fixture: 1;
- T2–T5: not started.

### Malformed second T1 response

A local SDK response supplied text Tool Call JSON with:

- `finish_reason=length`;
- `completion_tokens=256`;
- `delegated_completion_token_limit=256`;
- an unclosed `filter_records` JSON value;
- empty structured `message.tool_calls`.

Results:

- Provider requests: exactly 2;
- first `read_csv` executions: exactly 1;
- second tool executions: 0;
- retries: 0;
- replacement tasks: 0;
- T2–T5: not started;
- Checkpoint/Restore: not reached;
- the second response snapshot was persisted before parsing;
- its parsing failure was linked to the second audit request ID;
- the original `AgentParsingError` remained visible to the Runner.

This is a synthetic boundary test. It is not evidence that the historical real-Qwen
failure was caused by the 256-token limit.

## Frozen Protocol Evidence

No changes were made to:

- `configs/task_templates_v2.yaml` or any P2-D1 catalog/split/task identity;
- `configs/remote_model.yaml` or `configs/p2_d2_real_qwen_gate.yaml`;
- Prompt, Tool Schema, Observation, reference steps or verifier;
- Qwen model/version, Seed, Temperature, Completion/Context limits or budgets;
- Retryer/client retry policy;
- Checkpoint schema `2.0.0`, capture timing, Restore, Replay Guard, branch isolation or
  Hash implementation;
- the historical Run or its Artifact Manifests.

The existing five-task `run_fixed_gate()` and `gate` selection remain separate from
`run_single_t1()` and `single-t1-diagnostic`.

## Files Delivered

Audit Hardening:

- `src/risk_agent/monitoring/provider_response_audit.py`
- `src/risk_agent/monitoring/__init__.py`
- `src/risk_agent/agent/recording_model.py`
- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/collection/p2_d2_real_qwen_gate.py`
- `tests/agent/test_provider_response_audit.py`
- `tests/test_p2_d2_real_qwen_gate.py`
- `docs/phase2/daily_reports/p2_d2_audit_hardening_report.md`

T1 diagnostic preparation:

- `scripts/run_p2_d2_real_qwen_gate.py`
- fixed T1 Runner path and tests in the files above;
- this delivery report.

## Commands and Tests

```text
.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_provider_response_audit.py tests/test_p2_d2_real_qwen_gate.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Results:

- Provider audit plus P2-D2 diagnostic专项: 25 passed;
- full repository regression: 282 passed, 0 failed, 0 skipped;
- compileall: passed;
- diff check: passed.

The previous full-suite baseline was 278 passed. Four new fixed-T1 test functions raised
the total to 282 without removing or skipping an existing test.

## Resource Audit

| Operation | Stage A count |
| --- | ---: |
| Real Qwen requests | 0 |
| SSH operations | 0 |
| SSH tunnels | 0 |
| GPU queries/use | 0 |
| vLLM operations | 0 |
| Docker operations | 0 |
| Remote API requests | 0 |
| Formal experiment Runs | 0 |

All request behavior came from local FakeModel or HTTP MockTransport fixtures under
pytest temporary directories.

## Stage B Status

**NOT AUTHORIZED / NOT EXECUTED.**

No T1 real-Qwen diagnostic, remote preflight, SSH connection, tunnel, GPU inspection or
remote API request was performed. P2-D2 Real-Qwen Gate remains **BLOCKED**. A successful
local Stage A does not change the Gate result.

## Acceptance Checklist

- [x] Existing Audit Hardening reviewed and retained.
- [x] Fixed one-attempt T1 diagnostic entry prepared.
- [x] T1 success stops without T2.
- [x] T1 failure stops without retry, replacement or T2.
- [x] Malformed response, finish reason, Usage, delegated limit and parsing link tested.
- [x] Audit persistence failure still blocks tool execution.
- [x] Artifact Manifest coverage and six-Hash isolation tests pass.
- [x] Frozen protocol and five-task Gate semantics unchanged.
- [x] Full local regression, compile and diff checks pass.
- [x] Stage B not executed.

## Status: PASS

`P2-D2 Audit Delivery and T1 Diagnostic Preparation = PASS`

The repository is ready to request the separate Stage B authorization:

`授权执行 P2-D2 T1 Real-Qwen Diagnostic`

## Git Commit

Stage A authorizes one normal commit and fast-forward push to `origin/main`. The final
Commit SHA, remote update and clean-worktree result are recorded in the final handoff
after Git delivery completes.
