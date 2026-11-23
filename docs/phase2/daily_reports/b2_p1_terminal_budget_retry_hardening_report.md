# B2 Final Engineering Hardening Report

## Objectives

- Prevent ordinary task tools from running after Model Call, Token, latency or Tool Call budget
  reaches its exact terminal value, while still permitting a terminal `final_answer`.
- Freeze and continuously validate the effective smolagents Retryer and OpenAI Client retry
  configuration.
- Prove the complete local `RecordingModel -> OpenAIServerModel -> OpenAI SDK -> HTTP transport`
  path performs exactly one HTTP attempt on a simulated 429 response.

## Local Environment Work

- Date: 2026-11-23 (Asia/Shanghai).
- Baseline branch: `master`.
- Baseline HEAD and `origin/main`: `e66458ad51372a08ee4744b371ec7159fe1d901d`.
- Initial worktree: clean.
- Python: 3.12.3 from `.venv/bin/python`.
- Baseline tests: 201 passed.

## Remote Environment Work

None. Qwen requests: 0; real network requests: 0; GPU use: 0; SSH sessions: 0; vLLM
operations: 0; Docker operations: 0. The HTTP 429 acceptance test used an in-process
`httpx2.MockTransport` and the real installed OpenAI SDK call path.

## Work Completed

### Root Cause and Call Chain Before the Fix

Terminal budget chain:

```text
RecordingModel.generate()
  -> current request reaches Model Calls 10/10 or Tokens/latency exact limit
  -> _record_observed_cost() reports no strict overage
  -> CheckpointableToolCallingAgent.process_tool_calls()
  -> require_tool_response_allowed() passes
  -> Tool Call capacity preflight passes
  -> ordinary task tool executes and may commit a side effect
  -> only the following model request is rejected
```

The existing strict `>` post-response check was correct for distinguishing actual overage, but it
did not distinguish an ordinary task tool from terminal `final_answer` at exact equality. A mixed
task-tool plus FinalAnswer batch was also rejected by upstream smolagents only after entering its
parallel execution path, which was too late to guarantee zero side effects.

Retry chain:

```text
RecordingModel fingerprint
  -> model/client fields checked
  -> underlying ApiModel.retryer not checked
  -> changed retry attempts/predicate/wait policy could reach Provider delegation
```

The factory already configured `retry=False` and OpenAI `max_retries=0`, but the effective runtime
Retryer was neither validated nor frozen, and no test had traversed the real OpenAI SDK HTTP path.

### Call Chain After the Fix

```text
complete Assistant Tool Call batch
  -> reject mixed/multiple FinalAnswer batch
  -> reject strict post-response overage or unavailable Token usage
  -> if ordinary task tools exist, apply terminal Model/Token/latency/Tool guard
  -> preflight the complete Tool Call capacity
  -> execute tools
```

Exact exhaustion therefore blocks ordinary tools but permits a sole `final_answer`. Strict Token
or latency overage and unavailable usage retain the existing fail-closed behavior for every Tool
Call, including FinalAnswer. A failed tool cannot bypass the terminal guard on a later step.

For production `ApiModel` instances, RecordingModel now validates and fingerprints the Retryer and
real Client at binding, at normal request validation, and again immediately before delegation
under the existing lock. Retryer and Client object replacement is checked with private runtime
references. Object addresses are not serialized or exposed through `model_fingerprint`.

## Retryer Source Audit

The audit used `inspect` against the current `.venv`, not documentation assumptions.

| Item | Observed value |
|---|---|
| smolagents | 1.26.0 |
| smolagents model source | `.venv/lib/python3.12/site-packages/smolagents/models.py` |
| Retryer source | `.venv/lib/python3.12/site-packages/smolagents/utils.py` |
| OpenAI SDK | 3.52.0 |
| httpx2 | 2.16.2 |
| Tenacity | not installed |
| Production MRO | `OpenAIModel -> ApiModel -> Model -> object` |
| Retryer type | `smolagents.utils.Retrying` |
| Effective stop policy | `max_attempts=1` when `retry=False` |
| Retry policy | `smolagents.models.is_rate_limit_error` |
| Wait policy fields | `wait_seconds=60`, `exponential_base=2`, `jitter=True` |
| Reraise | `True` |
| OpenAI Client `max_retries` | `0` in both production factories |
| `client_kwargs["max_retries"]` | `0` in both production factories |

This smolagents release does not use Tenacity and has no `retryer.stop`, `retryer.retry` or
`retryer.wait` objects. Its effective equivalents are the fields above. The implementation checks
the actual installed `Retrying` type and these fields; it does not invent Tenacity attributes.

## Retryer Fingerprint Structure

The secret-free fingerprint adds:

```text
retryer:
  type
  stop_policy: {type: max_attempts, max_attempts: 1}
  retry_policy: {module, qualname}
  wait_policy: {type, wait_seconds, exponential_base, jitter}
  reraise
client:
  type
  base_url
  timeout
  max_retries: 0
client_kwargs:
  max_retries: 0
  sensitive values represented only by presence metadata
```

Unknown Retryer types, unreadable fields, attempts other than one, an unpinned predicate, invalid
wait fields, nonzero/missing real Client retry count, or inconsistent `client_kwargs` all fail
closed. Replacing the Retryer or Client object is rejected even when the replacement appears to
have equivalent public values. Scripted/non-ApiModel fixtures remain outside production Retryer
validation and continue to use the ordinary identity fingerprint.

## Files Changed

- `src/risk_agent/agent/recording_model.py`: terminal task-tool guard, validated Retryer/Client
  fingerprint, runtime object replacement checks, and removal of object addresses from public
  fingerprints.
- `src/risk_agent/agent/checkpointable_tool_agent.py`: mixed-batch preflight and ordered terminal
  guard before existing whole-batch Tool Call preflight.
- `tests/agent/test_checkpointable_agent.py`: exact terminal boundaries, mixed-batch zero-side-
  effect and tool-error bypass tests.
- `tests/agent/test_multistep_resume.py`: strengthens the exact-Token continuation assertion from
  the obsolete side-effect behavior to zero replay/zero new side effect.
- `tests/agent/test_retry_hardening.py`: factory, runtime tamper, fail-closed binding and real HTTP
  429 transport tests.
- `docs/phase2/daily_reports/b2_p1_terminal_budget_retry_hardening_report.md`: this report.

No production HTTPS client factory, dependency version, protocol, schema or frozen budget value
was changed.

## Terminal Budget Guard Tests

- Model Calls 9/10 after the current response: ordinary tool executes once.
- Model Calls 10/10 after the current response: ordinary tool executes zero times; sole
  FinalAnswer succeeds.
- Tokens `49,999 + 1 = 50,000`: ordinary tool executes zero times; sole FinalAnswer succeeds.
- Latency `899.75 + 0.25 = 900.0`: ordinary tool executes zero times; sole FinalAnswer succeeds.
- Token 50,001 and latency 900.25: existing strict overage still blocks FinalAnswer.
- Tool Calls 11/12 and 12/12, plus insufficient multi-call capacity: existing preflight semantics
  remain passing.
- Mixed ordinary tool plus FinalAnswer: rejected before execution; tool count, Tool History and
  workspace mutation remain zero.
- A failing tool at Model Calls 9/10 consumes one Tool Call; the next response reaches 10/10 and
  its ordinary tool is rejected, proving the failure path cannot bypass the guard.
- Restored exact-Token continuation: the new task tool does not change `counter.txt`, and no new
  Tool History entry is created; the next Provider request is rejected locally.

## HTTP 429 Single-Attempt Evidence

The test creates a real installed `OpenAIServerModel`, its real OpenAI 3.52.0 Client and an
`httpx2.Client` backed by `httpx2.MockTransport`. The transport returns HTTP 429 with an OpenAI
error body.

Observed assertions:

- Transport calls: exactly 1.
- URL: `https://worker.test/v1/chat/completions`.
- OpenAI Client `max_retries`: 0.
- smolagents Retryer `max_attempts`: 1.
- `RecordingModel.executed_calls`: 1.
- `RecordingModel.succeeded_calls`: 0.
- Model Calls consumed: 1/1.
- Prompt/Completion usage: unavailable (`None`), not fabricated as zero.
- Second generation attempt: locally rejected as `model_call_budget`.
- Additional Transport calls after rejection: 0.

## Commands and Tests

Final commands were executed in the requested order after the implementation stabilized:

| Command | Passed | Failed | Skipped | Pytest duration |
|---|---:|---:|---:|---:|
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_retry_hardening.py` | 10 | 0 | 0 | 0.53s |
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_checkpointable_agent.py` | 60 | 0 | 0 | 1.49s |
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/` | 83 | 0 | 0 | 1.96s |
| `.venv/bin/python -m pytest -q --disable-warnings` | 222 | 0 | 0 | 11.93s |

`.venv/bin/python -m compileall -q src tests` exited 0 with no output. `git diff --check`
exited 0 with no output. No test was skipped. The suite increased from 201 to 222 tests.

## Test Results

- P1-1 Terminal Budget Guard: **PASS**.
- P1-2 Retryer Fingerprint: **PASS**.
- HTTP 429 Single Attempt: **PASS**.
- Full Regression: **PASS**.
- B2 Final Engineering Gate: **PASS**.

## Acceptance Checklist

- [x] Exact terminal budgets block ordinary tools only.
- [x] Sole FinalAnswer remains valid at exact exhaustion.
- [x] Strict overage/unavailable usage remains fail closed.
- [x] Mixed FinalAnswer/task-tool batches have zero side effects.
- [x] Existing Tool Call count and full-batch capacity guards remain active.
- [x] Tool failure cannot bypass terminal Model Call exhaustion.
- [x] Actual installed Retryer implementation was inspected.
- [x] Retryer and Client retry behavior is validated at binding and every request boundary.
- [x] Runtime configuration mutation and object replacement fail before Provider execution.
- [x] Public fingerprints contain no secret or object address.
- [x] Real OpenAI SDK HTTP 429 path makes exactly one Transport request.
- [x] Full repository regression passes.

## Known Issues

- The HTTP acceptance test covers the real local SDK/transport stack but deliberately does not
  contact a remote service. Real Qwen/vLLM behavior remains unverified in this local Gate.
- Retry validation is intentionally pinned to smolagents 1.26.0's concrete `Retrying` semantics.
  A future smolagents upgrade must be audited and will fail closed until supported.
- The earlier limitation remains: without the exact remote Qwen tokenizer/chat template, Prompt
  Tokens cannot be guaranteed exactly before a request. Actual returned usage remains
  authoritative and strict overage fails closed.

## Remaining Work

No additional local B2 engineering work is required for these two issues. A real-Qwen Gate may be
considered only after separate user approval and the repository's shared-GPU, TLS, identity and
resource preflight. This report is not authorization to start that Gate.

## Status: PASS

All B2 Final Engineering Hardening acceptance conditions requested in this task passed with local
evidence. No real service, GPU or formal experiment was used.

## Git Commit

- Baseline/current HEAD: `e66458ad51372a08ee4744b371ec7159fe1d901d`.
- Branch: `master`, tracking `origin/main` at the same baseline commit.
- Commit created: no.
- Push performed: no.
- Worktree: intentionally contains the uncommitted implementation, tests and this report for
  human review.

