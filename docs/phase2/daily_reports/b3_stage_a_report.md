# B3 Stage A Report

## Objectives

- Implement the complete model-backed CONTINUE lifecycle using `agent.resume(stream=True)`.
- Keep the B2 controlled one-request boundary distinct from B3 full continuation.
- Add an outer outcome evaluator that separates Agent, tool, budget, model-service,
  environment, verification and manual-abort outcomes before deriving `Y_continue`.
- Prepare an offline-default B3 entry point without running SSH, GPU, vLLM or Qwen operations.

## Git Baseline

- Task design baseline: `da4bd76bd137b96f7c55ca262d18c085d57f6aa1`.
- Stage A starting HEAD: `2d9125687f7647a1c186d1263810e932867aedda`.
- The intervening commit only adds the repository automatic-delivery rule.
- Branch: `master`.
- Existing user file `docs/phase2/daily_reports/b2_day5_real_qwen_report.md` was preserved and
  not treated as a B3-created file.
- This task explicitly prohibits automatic Commit and Push; neither was performed.

## B2 Checkpoint Audit

- Fixed source Run: `b2_day5_five_gate_20261123_001`.
- Checkpoints readable: 5/5.
- Unique Task IDs / Task Run IDs / Checkpoint IDs: 5/5/5.
- Schema v2.0.0: 5/5.
- B2 Gate PASS records: 5/5.
- `controller_type=qwen3_model`: 5/5.
- `execution_mode=model_backed_smoke`: 5/5.
- Six Hash groups verified: 30/30.
- Completed/next step: 1/2 for every Checkpoint.
- Model/Tool Call usage at capture: 1/1 for every Checkpoint.
- Full audit: `b3_day_a1_checkpoint_audit.md` and the Stage A run artifact.

## Resume Lifecycle Audit

The B3 runner uses only `CheckpointableToolCallingAgent.resume(stream=True)` and fully consumes the
stream until `FinalAnswerStep`, a frozen budget termination, an Agent error, a model-service
failure, an environment failure or manual cancellation. It does not call
`request_next_model_response()` as the continuation result and does not use `run(reset=False)`.

The implementation:

- restores the Step cursor from completed Step 1 to Step 2;
- restores Model, Tool, Token and latency counters without resetting them;
- installs the exact first-request contract before Provider delegation;
- keeps model identity, Worker, Endpoint, generation config, tools, budgets and no-retry
  fingerprint guarded on later requests;
- applies the exact contract once, then permits later requests with continuing identity/budget
  guards;
- records ActionStep, PlanningStep, Tool Observation and FinalAnswerStep events;
- saves the final real Agent Memory and final isolated environment state;
- closes the stream without advancing it and verifies this with a no-extra-request test;
- blocks a source-completed canonical tool call before its local implementation can execute;
- maps every newly executed tool event back to its Tool Call ID.

## Outcome Evaluator

`ContinueOutcomeEvaluator` is separate from `DeterministicVerifier`. It invokes the Verifier only
for observable Agent/budget outcomes and preserves infrastructure/manual/unverifiable outcomes as
`Y_continue=null`. A Tool Error is recorded independently and the final Verifier still decides
1/0. It never converts null to zero.

Every outcome contains lineage to the source Checkpoint and Task Run, branch and attempt IDs,
restored combined Hash, actual first-input Hash, model/tool event IDs, final Agent state, final
environment state and Verifier outcome.

All Stage A and future Stage B outputs remain:

```json
{
  "formal_experiment": false,
  "execution_mode": "model_backed_smoke",
  "controller_type": "qwen3_model",
  "research_dataset_eligible": false
}
```

## B3 Entry Point

`scripts/run_b3_continue_gate.py` supports:

- `local-preflight` (default, network-free);
- `remote-preflight`;
- `single-continue`;
- `five-continue-gate`.

Every remote mode fails before creating its output directory unless `--authorize-remote` is
present following separate Stage B approval. Remote execution also requires approved evidence,
retains the original Worker per Checkpoint and does not perform failover. The runner never starts,
restarts, downloads or stops vLLM by itself.

Run directories use `exist_ok=False`. B2 Checkpoints are read-only inputs. Branch workspaces and
SQLite files are independent, JSONL files are append-only, and the original complete Checkpoint
inventory is compared before and after each attempt.

## Files Changed

- `src/risk_agent/collection/benchmark_tools.py`
- `src/risk_agent/collection/continue_outcome.py`
- `src/risk_agent/collection/continue_gate.py`
- `src/risk_agent/collection/__init__.py`
- `scripts/run_b3_continue_gate.py`
- `tests/agent/test_continue_outcome.py`
- `tests/agent/test_b3_continue_gate.py`
- `docs/phase2/daily_reports/b3_day_a1_checkpoint_audit.md`
- `docs/phase2/daily_reports/b3_stage_a_report.md`

No frozen Protocol, label definition, Checkpoint schema, task/split, prompt, model parameter,
budget, Verifier success rule or local/remote boundary was changed. RETRY and REPLAN were not
implemented or executed.

## Artifacts Generated

Offline Stage A run:

`runs/phase2_b3_continue_gate/b3_stage_a_20261123_001/`

It contains `run_manifest.json`, `checkpoint_audit.json`, `local_preflight.json` and
`artifact_manifest.json`. It made zero network requests and contains no B3 label.

## Commands and Tests

| Command | Result |
|---|---|
| B3 local preflight over the five real B2 Checkpoints | PASS; 5/5; zero network requests |
| Five independent positive/negative Verifier probes | 5/5 positive PASS; 5/5 negative rejected |
| B3 outcome and full-stream integration tests | PASS |
| B2 multi-step Resume and Day 5 wiring regression | PASS |
| `.venv/bin/python -m pytest -q --disable-warnings` | 242 passed |
| `.venv/bin/python -m compileall -q src tests` | exit 0 |
| `git diff --check` | exit 0 |

The production-path integration test uses the OpenAI-compatible SDK with `MockTransport`. It
captures a source Checkpoint, performs the separate B2 controlled request, independently restores
the Checkpoint, then consumes a two-request B3 stream containing a new local Tool Call followed by
a FinalAnswer. The test verifies exact first input, different later input, continuous budgets,
Memory updates, Tool Call ID lineage, Verifier output, no source-call replay, immutable Checkpoint
inventory and no request on Generator close.

## Acceptance Checklist

- [x] Five real B2 Checkpoints exist and verify without mutation.
- [x] All six Hash groups pass for all five Checkpoints.
- [x] Complete multi-round `resume(stream=True)` passes offline integration.
- [x] First restored request is byte/Hash exact; later requests do not reuse the one-shot check.
- [x] Step, Model, Tool, Token and latency budgets remain continuous.
- [x] Agent Memory receives new ActionStep/Observation/FinalAnswer state.
- [x] Normal final answer, Agent/tool error, max/budget, model-service and environment/null outcome
  paths are covered by lifecycle and evaluator tests.
- [x] Source-completed tool replay is blocked before local execution.
- [x] Existing F6 committed/unknown blind-retry protections remain covered by historical tests.
- [x] Independent branch state and immutable Checkpoint inventory are tested.
- [x] Verifier positive/negative behavior is confirmed for all five fixed tasks.
- [x] Remote modes require separate authorization before any output or network activity.
- [x] No SSH, GPU, vLLM or real Qwen operation occurred in Stage A.
- [x] B3 outputs are permanently marked non-formal and dataset-ineligible.

## Stage B Resource Estimate

- Expected Qwen requests: 5. The Day 5 controlled responses for all five exact first inputs were
  `final_answer` calls. Actual B3 execution remains authoritative.
- Frozen hard upper bound from remaining budgets: 45 model requests and 55 new task-tool calls
  across five branches; the runner stops at the per-task limits.
- Expected new local task-tool operations: 0; possible actual count is model-determined.
- Required Workers: original `worker-0` for tasks 1/3/5 and `worker-1` for tasks 2/4.
- Remote service change: none expected. Stage B must reuse and re-verify the two existing Workers;
  it is not authorized to start/restart/stop/download services.
- Estimated elapsed time after tunnels/preflight: about 5–15 minutes for the expected path; each
  task retains the frozen 900-second wall limit.
- Local disk: about 943 GiB free; Stage B is expected to use far below 1 GiB and exceeds the
  required 20 GiB safety floor.
- Remote GPU: no additional allocation expected when the existing project-owned Workers remain
  healthy; their current ownership, GPU mapping and shared-resource status must still be sampled
  and verified twice under Stage B authorization.

## Known Issues and Remaining Gate

- Stage A used ScriptedModel/MockTransport for full-stream execution; no B3 real Qwen request has
  been made.
- Historical physical GPU IDs are not a current lease. Worker PID, GPU UUID, two occupancy
  samples, TLS, `/v1/models`, weight identity and service health must be re-verified.
- Exact pre-delegation context-token count remains unavailable from the current Provider boundary;
  no silent truncation/summarization is enabled and returned Provider Usage remains authoritative.
- The five future labels are engineering smoke evidence only, irrespective of their 1/0/null
  distribution. They must not enter formal Train/Validation/Test or tune Prompt/budgets.

## Status: PASS

B3 Stage A local engineering and offline acceptance are PASS. B3 itself is not yet PASS because
no real Qwen CONTINUE chain has been executed. Stage B, B4, formal collection, RETRY and REPLAN
remain not authorized and not started.

## Git Commit

- Commit created: no, per this B3 task's explicit Git rule.
- Push performed: no.
