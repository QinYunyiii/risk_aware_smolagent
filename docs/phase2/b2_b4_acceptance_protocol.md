# B2--B4 Acceptance Protocol

> Protocol ID: `risk-continue-v2.0.0`
> This document defines future Gates. It is not evidence that any Gate has passed.

## 1. Common preconditions

Before any real Qwen/GPU test:

- the user has approved B1;
- Git status and existing user changes are recorded;
- `smolagents==1.26.0` and model client versions match the manifest;
- Prompt/tool/verifier/catalog/config hashes are frozen for the run;
- remote identity, TLS CA verification, Worker ownership and physical GPU mapping are recorded;
- at most two lab-approved free GPUs are selected after two samples;
- output directory is new, append-only guards are active, disk has at least 20 GB free;
- stop/cleanup steps and run-specific process ownership are recorded.

## 2. B2 — Complete decision Checkpoint

### Required implementation evidence

1. Capture uses the streamed ActionStep boundary after memory append and before the generator is
   advanced.
2. Exact canonical Messages, full Memory steps, System/Task Prompts, Assistant tool-call IDs and
   Tool Observations are saved.
3. Agent step/max-step/state, tool registry, generation config, model identity and remaining
   budgets are saved.
4. Workspace files, SQLite Backup API snapshot/logical state, Mock API, idempotency and side-effect
   state are saved.
5. Runtime/tool/fault adapter counters and future-behavior state are saved.
6. v1 readers remain compatible; v1 is never reported as complete Agent Restore.

### Exact next-input acceptance test

At capture, serialize `agent.write_memory_to_messages()` to canonical JSON bytes and hash them.
After Restore, install a recording model wrapper at the first `model.generate()` boundary. Before
allowing the request to proceed, compare byte-for-byte:

- ordered roles and content blocks;
- Assistant tool-call ID/name/normalized arguments;
- Tool Response content;
- System and Task Prompt content;
- ordered public tool schemas;
- generation configuration and remaining step number.

The intercepted first input hash must equal `next_model_input_hash`. A semantic-only or text-only
comparison is insufficient. Then one controlled request must prove the restored Agent can invoke
Qwen; it must not produce a formal label unless B3 completes.

### Tests and Gate

- unit: canonical message/tool-call round trip, null vs zero, corruption rejection, v1/v2
  dispatch, hash normalization and volatile-field exclusions;
- lifecycle spy: model call count remains 1 through capture; becomes 2 only after Restore
  CONTINUE starts;
- integration: two independent restores have identical component hashes but different workspace
  and SQLite files; branch A mutation does not affect branch B;
- real capture: at least five Qwen tasks produce five complete Checkpoints;
- 5/5 source/restored Agent, Environment, Runtime and combined hashes match;
- 5/5 first resumed input bytes/hash match;
- 5/5 Assistant call/Observation pairs and remaining budgets are complete.

Any mismatch, missing Agent payload, or inability to issue the controlled next Qwen request makes
B2 `BLOCKED`. An environment-only Restore cannot receive conditional credit.

## 3. B3 — Model-backed CONTINUE and label

For at least five valid Checkpoints:

1. create a unique isolated branch workspace;
2. verify all source artifacts and restored hashes;
3. verify exact first model input;
4. call the same Qwen3-8B model/Worker with unchanged Prompt/tools/generation config;
5. add no intervention message and do not replay the completed tool call;
6. execute real local Tool Calls and store exact Agent-visible Observations;
7. record Prompt/Completion Tokens, model/tool/wall latency with missingness status;
8. classify model-service, environment, Agent, tool, budget and verifier outcomes separately;
9. invoke the versioned deterministic verifier;
10. derive `Y_continue` as 1/0/null with full lineage.

B3 passes only if 5/5 complete Restore -> Qwen CONTINUE -> Verifier chains finish with traceable
model identity and no state cross-talk. Infra-failed attempts may be null but do not count toward
the five completed chains. Missing critical cost fields above the 5% allowance blocks the Gate.

### Retry rule during B3

One safe reattempt is allowed only when failure occurred before any tool call/side effect and the
workspace hash is unchanged. It uses a new attempt ID and retains the failure record. No automatic
reattempt is permitted after a write, `committed`/`unknown` state, or uncertain snapshot.

## 4. B4 — Non-formal smoke

Run 8--12 Train or dedicated development tasks, covering:

- all four domains;
- at least four fault types overall, including F0 and at least one error response;
- at least five valid primary Checkpoints;
- complete CONTINUE for at least five Checkpoints.

Every record declares:

```json
{
  "formal_experiment": false,
  "execution_mode": "model_backed_smoke",
  "controller_type": "qwen3_model"
}
```

Smoke data is permanently excluded from formal Train/Validation/Test. It may be used only for
engineering diagnosis and resource estimation.

### B4 Gate

- orchestration completes for 8--12 attempted tasks without unhandled harness error;
- at least 5 Checkpoints pass all B2 Restore/input checks;
- at least 5 full model-backed CONTINUE chains pass B3 lineage checks;
- verifier positive and deliberate negative probes behave as specified;
- Token and latency status is valid, and null differs from real zero;
- no workspace/SQLite/Mock API cross-talk;
- F6 unknown/committed cannot blind-retry;
- Worker/tunnel/owned remote processes are stopped only when confirmed run-owned;
- physical GPU IDs, check times and release state are logged;
- smoke artifacts are absent from formal dataset manifests.

If an implementation defect is found, preserve the failed run, fix under a new code/config hash,
and repeat B4. Do not patch a completed run or lower the Gate.

## 5. Transition decision

B2, B3 and B4 are sequential hard Gates. Formal Train collection remains blocked until all three
pass and the v2 multi-step catalog/split/fault schedule has its own frozen hash. Passing B4 proves
engineering viability only; it does not validate risk-prediction performance.
