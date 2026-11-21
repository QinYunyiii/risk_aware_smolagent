# B2 P1 Budget and Model Identity Hardening Report

## Objectives

- Enforce the frozen Tool Call limit before any local tool side effect.
- Fail closed when an observed model response crosses the Token or latency budget.
- Bind every delegated request to an immutable, secret-free fingerprint of the actual underlying
  model and OpenAI-compatible client configuration.
- Preserve Protocol v2, Checkpoint Schema v2.0.0 and all previously accepted behavior.

## Local Environment Work

All implementation and tests ran in the project `.venv` on local WSL. The baseline branch was
`master`; both `HEAD` and `origin/main` were
`2f13b57ea854278bf2bb93610a1cdf48ba5b810d`, and the initial worktree was clean. The installed
smolagents version remains pinned to 1.26.0.

## Remote Environment Work

None. Qwen requests: 0; GPU use: 0; SSH sessions: 0; vLLM operations: 0; Docker operations: 0.

## Work Completed

### P1-1 Tool Call Budget

- `CheckpointableToolCallingAgent.process_tool_calls()` now materializes and checks the entire
  non-FinalAnswer batch before delegating to smolagents. A batch larger than the remaining
  capacity is rejected with `tool_call_budget` before any call is yielded or executed.
- `final_answer` is not charged as a task-tool call. Therefore 12/12 still permits the final
  model response and FinalAnswer execution, while any new task tool is rejected.
- Every actual task-tool attempt is counted immediately before `execute_tool_call()`. A failed
  attempt consumes one call; a preflight rejection consumes none.
- `ExecutionMonitor` performs the same pre-side-effect count for direct monitored execution.
  A ContextVar propagated into smolagents worker threads identifies Agent-precounted calls and
  prevents monitor double counting.
- `BudgetState` rejects construction/restoration above the Tool Call limit and rejects increments
  that would cross it. The tested invariant is `0 <= tool_calls_used <= max_tool_calls`.

### P1-2 Token and Latency Budget

- Every request still checks cumulative usage before Provider delegation.
- RecordingModel passes a completion cap no larger than the remaining Token budget. Inspection of
  smolagents 1.26.0 confirmed that its OpenAI-compatible `max_tokens` is forwarded as a completion
  limit and that `Model.kwargs` has highest precedence; the implementation therefore applies the
  cap in a scoped, lock-protected override and restores the original configuration afterward.
- The remaining latency budget is passed as a per-request timeout, bounded by any smaller caller,
  model or client timeout that can be read.
- Actual response Prompt + Completion usage and measured latency remain authoritative. Exact
  equality is accepted for the current response. A strict overage is recorded without clamping as
  `budget_violation` with `token_budget_exceeded` or `latency_budget_exceeded`.
- A response marked with an overage cannot execute any Tool Call. A subsequent generation attempt
  is rejected locally before Provider delegation.
- Provider failure records measured latency and missing Token usage. Missing usage is not changed
  to zero; subsequent generation fails closed as `token_usage_unavailable`.
- A successful response without usable Token usage also fails closed before Tool Call execution.

### P1-3 Underlying Model Identity

- RecordingModel freezes a canonical fingerprint at construction and rechecks it both during
  request validation and immediately before Provider delegation under the delegation lock.
- The fingerprint covers the actual delegated object's class, Model ID, Generation Config,
  endpoint/base URL from configuration and the live client, client timeout/no-retry settings,
  message/tool argument conventions, and model version/worker identity when present.
- Mutable non-JSON request objects are bound by type and object identity. Secrets such as API keys,
  Authorization values, passwords and access tokens are represented only by presence metadata.
- Mutating the wrapper Model ID/config or the underlying Model ID, Generation Config, configured
  endpoint, or live client endpoint rejects the request before the underlying `generate()` call.
- The fingerprint is never auto-refreshed. Existing HTTPS client construction and `max_retries=0`
  behavior were not changed.

## Files Changed

- `src/risk_agent/agent/recording_model.py`: response overage guard, request limits and immutable
  underlying-model fingerprint.
- `src/risk_agent/agent/checkpointable_tool_agent.py`: whole-batch preflight and universal
  task-tool attempt accounting.
- `src/risk_agent/monitoring/runtime_state.py`: hard Tool Call increment/restoration invariant.
- `src/risk_agent/monitoring/execution_monitor.py`: pre-side-effect counting and Agent/monitor
  deduplication.
- `src/risk_agent/monitoring/__init__.py`: exports for the budget exception/accounting context.
- `tests/agent/test_checkpointable_agent.py`: Tool, Token, latency and identity boundary coverage.
- `tests/test_checkpoint.py`: direct monitor preflight and deduplication coverage.
- `tests/test_runtime_state.py`: hard Tool Call upper-bound coverage.
- `docs/phase2/daily_reports/b2_p1_budget_identity_hardening_report.md`: this report.

## Artifacts Generated

This report only. No formal experiment trajectory, Checkpoint, dataset, remote artifact or mock
result was presented as research evidence.

## Commands and Tests

Commands were executed in the required order after the final code change:

| Command | Passed | Failed | Skipped | Pytest duration |
|---|---:|---:|---:|---:|
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_checkpointable_agent.py` | 49 | 0 | 0 | 1.33s |
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/test_multistep_resume.py` | 5 | 0 | 0 | 0.81s |
| `.venv/bin/python -m pytest -q --disable-warnings tests/test_runtime_state.py` | 13 | 0 | 0 | 0.01s |
| `.venv/bin/python -m pytest -q --disable-warnings tests/agent/` | 62 | 0 | 0 | 1.70s |
| `.venv/bin/python -m pytest -q --disable-warnings` | 201 | 0 | 0 | 10.77s |

`.venv/bin/python -m compileall -q src tests` exited 0 with no output. `git diff --check` exited 0
with no output. No historical test was deleted, weakened or skipped. The baseline had 180 tests;
21 tests were added.

## Test Results

### Tool Call Boundaries

- 11/12 plus one successful tool: allowed, one side effect, final count 12.
- 12/12 plus FinalAnswer: allowed, final count remains 12.
- 12/12 plus one task tool: whole response rejected, zero tool executions, count remains 12.
- 11/12 plus two task tools: whole batch rejected, zero tool executions, count remains 11.
- 11/12 plus one failing tool: one attempted execution, final count 12.
- Direct `ExecutionMonitor` call at its limit: rejected before the supplied callable runs.
- Agent-precounted monitored call: one history entry and exactly one Tool Call charge.

### Token Boundaries

- 49,999 + 1 observed Token: exactly 50,000, current response accepted.
- 49,999 + 2 observed Tokens: real cumulative usage 50,001 retained, response marked
  `token_budget_exceeded`, zero tool executions and no later Provider call.
- Completion `max_tokens` was reduced to 1 at the boundary and the underlying model's configured
  value was restored after delegation.
- Provider failure kept cumulative Tokens unchanged and marked usage unavailable rather than zero.

### Latency Boundaries

- 899.0 + 0.5 seconds: 899.5, accepted.
- 899.0 + 1.0 seconds: exactly 900.0, current response accepted.
- 899.0 + 1.25 seconds: real cumulative latency 900.25 retained, response marked
  `latency_budget_exceeded`, zero tool executions and no later Provider call.
- Timeout failure recorded 1.25 seconds and missing Token usage; the next request failed closed.
- The remaining 1.0-second latency budget was passed to the delegated request as its timeout.

### Model Identity Boundaries

- Unchanged normal and restored model configurations passed all historical lifecycle tests.
- Wrapper Model ID, underlying Model ID, underlying Generation Config, configured base URL and
  live client base URL mutations all rejected before Provider execution.
- Fingerprints retained model version and worker identity when supplied.
- The fixture secret was absent from the fingerprint; only `{present: true, sensitive: true}` was
  retained.

## Acceptance Checklist

- [x] Whole Tool Call batch is checked before side effects.
- [x] Failed task tools count once; rejected calls count zero.
- [x] Tool Call usage cannot be incremented above 12 through BudgetState/monitor/Agent paths.
- [x] Exact Token/latency boundaries and strict overages are distinct.
- [x] Actual overage is recorded and response Tool Calls are blocked.
- [x] Provider failure never fabricates zero Token usage.
- [x] Completion length and request timeout use remaining-budget caps where supported.
- [x] Wrapper and actual underlying model identities are checked before each delegation.
- [x] Endpoint/config mutation cannot call the Provider.
- [x] Secrets are not stored in fingerprint evidence.
- [x] All required and full regression tests pass.

## Known Issues

- There is no exact local tokenizer wired to the remote Qwen chat template in this local-only run.
  Therefore the pre-request completion cap cannot account exactly for Prompt Tokens. It reduces
  avoidable Completion overrun, while actual Provider usage remains authoritative and a response
  overage fails closed. This is not an absolute pre-request total-Token guarantee.
- A client timeout bounds how long the local caller waits; it cannot prove that a remote Provider
  stopped execution at the same instant. Actual locally observed latency is still recorded.
- Generic opaque request objects can be protected against replacement by object identity, but
  arbitrary undocumented internal mutation cannot be exhaustively inspected without risking
  secret capture. The OpenAI-compatible live endpoint, timeout and retry-relevant public fields
  used here are explicitly fingerprinted.
- No real Qwen/vLLM/HTTPS service was invoked, so Provider-specific Token reporting and timeout
  behavior remain for the separately authorized real-service Gate.

## Remaining Work

Run the existing real-Qwen validation Gate only after separate user approval and the required
shared-GPU/resource preflight. No B3/B4/formal collection was started by this task.

## Status: PASS

All requested local P1 engineering gates pass. The limitations above are explicitly retained and
do not claim real-provider validation.

## Git Commit

- Current HEAD: `2f13b57ea854278bf2bb93610a1cdf48ba5b810d`.
- Branch: `master`, baseline matched `origin/main` before this work.
- Commit created: no.
- Push performed: no.
- Worktree: intentionally contains the uncommitted implementation, tests and this report for user
  review.
