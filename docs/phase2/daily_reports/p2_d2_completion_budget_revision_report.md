# P2-D2 Completion Budget Revision — Stage A Report

## Objectives

- Prepare a separately versioned T1 diagnostic with a 1,024-token per-request
  completion cap.
- Keep the historical 256-token configuration and five-task Gate unchanged.
- Change no other experimental variable, task, Prompt, Tool Schema, Observation,
  Checkpoint v2 rule, or total budget.
- Prove all behavior locally with MockTransport/Fake Provider fixtures only.

## Status

**P2-D2 Completion Budget Revision Stage A: PASS**

**P2-D2 Real-Qwen Full Gate: BLOCKED**

No SSH, remote HTTPS request, Qwen request, GPU query/use, vLLM operation, tunnel,
Docker operation, or formal experiment occurred in this Stage.

## Git Baseline

- Baseline commit: `ced6bb25125ef21f9f3e7568e0dd6a1f22bb88c6`.
- Branch: `main`.
- Baseline local HEAD and `origin/main`: identical.
- Starting worktree: clean.
- Historical Runs and their artifacts were not modified.

## Versioned Experiment Identity

The 1,024-token diagnostic has identities independent from the frozen 256-token Gate:

| Identity | Value |
| --- | --- |
| Checkpoint protocol | `risk-continue-v2.0.0` |
| Checkpoint schema | `2.0.0` |
| Experiment ID | `p2_d2_t1_completion_1024_diagnostic` |
| Experiment config version | `p2-d2-completion-budget-1024-v1` |
| Generation config version | `qwen3-8b-completion-1024-v1` |
| Diagnostic mode | `single-t1-budget-1024-diagnostic` |
| Fixed Task | `p2_data_region_units_sum_001` |
| Fixed Worker | `worker-0` |
| Output root | `runs/phase2_p2_d2_t1_budget_1024/` |

The new runner manifest records all five identities independently: protocol,
experiment, experiment-config version, generation-config version, and Run ID. It also
records the unchanged Catalog Hash.

## 256/1024 Configuration Isolation

The two historical files remain byte-for-byte unchanged:

| Configuration | Completion cap | SHA-256 |
| --- | ---: | --- |
| `configs/remote_model.yaml` | 256 | `1a347dcf96848d7ea4ea390987d0ebe4526b06f6143d70c5f379903feccc7436` |
| `configs/p2_d2_real_qwen_gate.yaml` | 256 | `56f1428791a76c8a5e8eed982251cc5d030472e55ebcbd8628d4a111ad5d89e5` |

New configurations:

| Configuration | Completion cap | SHA-256 |
| --- | ---: | --- |
| `configs/remote_model_p2_d2_1024.yaml` | 1,024 | `4731cb56c128e3c150712745e25b9881fd8c19d64a37fd87a97f48ae3a5a57a5` |
| `configs/p2_d2_t1_budget_1024.yaml` | 1,024 | `c3ff6fca4d00402567de8f6f183a9ae0333399ab66af1d50fc0a50a69bde93b3` |

The new model configuration is mechanically compared with the 256 baseline. After
removing its generation-version metadata and changing 1,024 back to 256, the complete
YAML objects must be equal. This proves the model configuration changes no endpoint,
model ID/version, Prompt version, API-key/CA environment variable, timeout, routing,
Worker, seed, temperature, or thinking setting.

Strict allowlisting enforces:

- the historical Gate accepts only its 256-token profile;
- the new experiment accepts only its registered 1,024-token profile;
- substituting 256, 512, or 2,048 in the new profile is rejected;
- loading the 1,024 model config through the old five-task entry is rejected before a
  Run directory is created;
- arbitrary Task IDs are not accepted by the new CLI.

Generation-config canonical hashes are distinct:

- 256: `8f837d23e1c4fc57423439eb2df59740e893ce70b5767c6fc6b62505b5ebb563`
- 1,024: `a1ff46cc4b80bc62e6eb24d8e464cb75f61c89f2f126907515f12b71229e88bc`

## Implementation

### Strict profile validation

`p2_d2_completion_budget.py` defines the only registered 1,024-token identity and
validates it against hashes of the historical configuration. Unknown token values and
changes to invariant fields fail closed.

### Independent diagnostic entry

`run_p2_d2_t1_budget_diagnostic.py` exposes only:

- `local-preflight`;
- `single-t1-budget-1024-diagnostic`.

The remote mode still requires both `--authorize-remote` and remote evidence. It has no
`--task-id`, Gate mode, multi-task mode, retry mode, or task-replacement path. Its
manifest fixes one T1 attempt on Worker 0 and explicitly forbids T2--T5.

### Existing Runner reuse

The existing P2-D2 Runner now accepts optional Experiment/config identity fields. The
defaults retain the old Experiment ID, diagnostic mode, Runtime state, and output
shape. Only the new entry supplies the 1,024-specific identity, which is included in
the Monitor, Checkpoint identity, Runtime tool state, attempt, and summary.

The five-task `gate` and historical `single-t1-diagnostic` entry remain bound to the
old strict 256 configuration.

## Model Request Delegation

The existing `RecordingModel` budget implementation required no change. Its delegated
cap is the minimum of:

1. the underlying configured per-request completion cap;
2. an explicit request cap, when present;
3. the remaining 50,000-token cumulative budget.

MockTransport inspected the actual HTTP request JSON at the provider boundary:

| Runtime state | Underlying cap | Actual `max_tokens` |
| --- | ---: | ---: |
| Fresh 50,000-token budget | 1,024 | 1,024 |
| 49,400 tokens already used | 1,024 | 600 |

The Provider-facing value was inspected, not inferred from YAML. The second case proves
that increasing the per-request cap does not weaken the cumulative total-token guard.
Provider audit continues to record this actual delegated limit.

## Synthetic Over-256 T1 Lifecycle

A local OpenAI SDK/MockTransport fixture returned:

1. a valid `read_csv` Tool Call;
2. a valid `filter_records` Tool Call with all six Observation rows and synthetic
   `completion_tokens=400`;
3. one controlled restored response.

All three HTTP request bodies carried `max_tokens=1024`. Results:

- one fixed T1 attempt;
- exactly two Source model calls and two dependent local tool calls;
- one eligible Decision Checkpoint;
- one independent Restore model request;
- six restored hashes matched;
- Source/Checkpoint/Restore first input matched exactly;
- Replay Guard blocked historical tool execution;
- no restored task tool executed;
- T2--T5 were not started;
- no automatic retry or replacement occurred.

This is synthetic engineering evidence only. It does not claim that real Qwen succeeds
with 1,024 tokens.

## Source/Restore and Cross-Version Protection

The successful synthetic Checkpoint stores `max_tokens=1024` in the Agent generation
configuration and the new Experiment ID in its identity. Its Runtime state preserves:

- two model calls;
- two tool calls;
- 50 prompt plus 431 completion tokens;
- accumulated non-negative model/tool latency;
- the same remaining 50,000-token total budget semantics.

The Restore uses the same 1,024 configuration and consumes the next Model Call without
resetting counters. Both mismatch directions were tested:

- 256 Source contract → 1,024 Restore model: rejected;
- 1,024 Source contract → 256 Restore model: rejected.

Both fail as `generation_config` mismatches before the underlying Provider is called.
Thus a 256 Checkpoint cannot silently continue under the 1,024 identity, and a 1,024
Checkpoint cannot masquerade as a 256 Restore.

## Provider Audit Compatibility

A synthetic response with `finish_reason=length`, `completion_tokens=1024`, and
incomplete Tool Call JSON verified:

- SDK-visible Provider snapshot persistence;
- raw response SHA-256;
- Finish Reason and complete Usage;
- actual delegated completion limit of 1,024;
- exact Provider/parsing audit-request correlation;
- `AgentParsingError` and `tool_call_parse_failure` classification;
- one successful first tool and zero second-tool executions;
- no retry, replacement, Checkpoint, Restore, or T2--T5.

Existing negative Audit Hardening tests continue to cover redaction and nullable absent
fields. No audit guarantee was weakened.

## Frozen Protocol Evidence

No changes were made to the Catalog, Task Prompt, reference steps, Tool Schema
implementation, Agent state adapter, Checkpoint v2 manager, Hash algorithm, Replay
Guard, verifier, or Provider audit implementation.

Frozen evidence:

- Catalog file SHA-256:
  `3cae7519dffd97cd105f1bfca87183f34d15bc7e613b611a44a810d4dfff1d4f`;
- canonical Catalog Hash:
  `c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c`;
- T1 Prompt Hash:
  `9c86b154c684d22a976ca1a4bff45b8c158c36a48f9d632c4b23bedb7aa509ec`;
- T1 reference-step Hash:
  `7d7c404dca887610c92a8d0fcd29d3367a23e3ce958ed1517953c20631c64334`;
- Checkpoint schema: `2.0.0`;
- total-token budget: 50,000;
- Agent/Model/Tool/wall-clock limits: 10 / 10 / 12 / 900 seconds;
- context limit and request timeout: 8,192 / 120 seconds;
- fault injection: disabled.

## Files Added

- `configs/remote_model_p2_d2_1024.yaml`
- `configs/p2_d2_t1_budget_1024.yaml`
- `src/risk_agent/collection/p2_d2_completion_budget.py`
- `scripts/run_p2_d2_t1_budget_diagnostic.py`
- `tests/test_p2_d2_completion_budget_revision.py`
- `docs/phase2/daily_reports/p2_d2_completion_budget_revision_report.md`

## Files Modified

- `src/risk_agent/collection/p2_d2_real_qwen_gate.py`

## Commands and Tests

```text
.venv/bin/python scripts/run_p2_d2_t1_budget_diagnostic.py --mode local-preflight ...
.venv/bin/python -m pytest -q --disable-warnings tests/test_p2_d2_completion_budget_revision.py
.venv/bin/python -m pytest -q --disable-warnings tests/test_p2_d2_completion_budget_revision.py tests/test_p2_d2_real_qwen_gate.py tests/agent/test_provider_response_audit.py tests/test_model_backed_checkpoint.py tests/test_checkpoint_v2.py tests/test_checkpoint_v2_isolation.py tests/agent/test_checkpointable_agent.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

Results:

- new completion-budget test file: 16 passed;
- completion-budget plus historical P2-D2 entry: 25 passed;
- Checkpoint/P2-D2/Provider Audit related regression: 145 passed;
- final full repository regression: 298 passed in 29.63 seconds;
- compileall: passed;
- diff check: passed.

The previous baseline was 282 passed. Sixteen new cases raised the total to 298 without
removing, skipping, or weakening an existing test.

## Resource Audit

| Operation | Count |
| --- | ---: |
| Real Qwen requests | 0 |
| SSH operations | 0 |
| SSH tunnels | 0 |
| Remote API requests | 0 |
| GPU queries/use | 0 |
| vLLM start/stop/restart | 0 |
| Docker operations | 0 |
| Formal experiment Runs | 0 |

All Provider behavior came from local HTTP MockTransport or local model fixtures in
pytest temporary directories.

## Acceptance Checklist

- [x] Historical 256 configs unchanged and hash-bound.
- [x] Independent 1,024 configs and identities created.
- [x] Fake Provider boundary received `max_tokens=1024`.
- [x] Remaining total-token budget reduced the delegated cap safely.
- [x] Historical five-task Gate remains strict 256.
- [x] Fixed one-attempt T1 mode prepared; T2--T5 cannot start.
- [x] Synthetic Source/Restore 1,024 path and six hashes passed.
- [x] 256/1,024 cross-version continuation rejected in both directions.
- [x] Provider Audit retained snapshot, finish reason, Usage, Hash and parsing link.
- [x] Checkpoint v2 Schema and Hash semantics unchanged.
- [x] Catalog, Tool Schema, Prompt, Observation and Replay Guard unchanged.
- [x] Full regression, compileall and diff checks passed.
- [x] Remote/GPU/Qwen operation count is zero.

## Known Limitations

- Real Qwen behavior with 1,024 completion tokens is not verified.
- 1,024 is a controlled engineering candidate, not a proven optimal value.
- The historical 256 failure established truncation, but removing that truncation may
  expose an independent model-output or parser-compatibility failure.
- The full five-task Gate remains blocked and P2-D3 must not begin.

## Next Authorization

Stage B requires a separate explicit user message:

`授权执行 P2-D2 1024 Token T1 Real-Qwen Diagnostic`

Without that exact authorization, do not connect by SSH, create a tunnel, contact the
remote API, query a GPU, operate vLLM, run T1, run the five-task Gate, or start P2-D3.

## Git Commit

Stage A authorizes one normal commit and fast-forward push after final test and security
review. The final Commit SHA and Push result are reported in the handoff because a
commit cannot contain its own SHA.
