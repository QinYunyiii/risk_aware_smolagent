# Protocol v2 Freeze Record

## Freeze identity

- Formal Protocol ID: `risk-continue-v2.0.0`
- Release candidate: `risk-continue-v2.0.0-rc1`
- Freeze time: `2026-11-20T15:44:56+08:00` (Asia/Shanghai)
- Source Git commit: `002df218adf10c7a792c9b4e3cb4c1d9a62b8c8d`
- Source branch mapping: local `master` tracks `origin/main`
- Remote: `https://github.com/QinYunyiii/risk_aware_smolagent.git`
- Remote source ref verified before freeze: `refs/heads/main` at source commit

The commit containing this record is the authoritative freeze commit. Its SHA is intentionally
not embedded into its own contents; resolve it with `git log -- docs/phase2/protocol_freeze_record_v2.md`.

## RC1 source SHA-256

These hashes were computed before any formal-version marker was changed.

| RC1 artifact | SHA-256 |
|---|---|
| `docs/phase2/protocol_risk_v2.md` | `982e08c17cce71086f96051ac35ef7fd7ad80c108729c9d9e6f174b8f899c66e` |
| `docs/phase2/schema_risk_v2.md` | `7e1d63667df558db1e844dcd0b4479d5d35f9026752cfc06306714a3829c3f32` |
| `docs/phase2/feature_allowlist.json` | `6f12d4d7fef56dd6e41013bb398bbae8a1e7326adc59adeb2c0507d48bdb5aa7` |
| `docs/phase2/checkpoint_schema_v2.json` | `1dc35c59c9bf6c3d6f3d7aba4a82d23cfea10cc5e29bf11f2c338db5289b9a40` |
| `docs/phase2/failure_taxonomy_v2.md` | `ffc50213dc8f4fbce331e4c53997f03ce84b397765bc026e6e7e609ef7048499` |
| `docs/phase2/task_split_manifest_v2.md` | `6585c59ee9e9928efd6d756b175ddea8fd3e270af966eea632bdad96d69358cb` |
| `docs/phase2/collection_budget_v2.md` | `57a9618ecfa38a3bfd6fa83ad44d540b250f21bb104457ba3984b084ae708a17` |
| `docs/phase2/b2_b4_acceptance_protocol.md` | `752fca62293edd4680acbc10dd45592241f8ce3899ab0fa4452913728e21b838` |
| `configs/phase2_risk_collection.yaml` | `8af0952490388d384ab2d8f17ab63c7206929e57b921892fd1d579b3cbceffb7` |

## Approval basis

The user task titled “B1 Freeze + B2 完整 Agent Checkpoint 实现任务” explicitly directs this
freeze and states that the following recommended decisions are adopted for the freeze. This
record treats those listed decisions as approved for Protocol v2, without treating unlisted
implementation claims or remote-resource use as approved.

| Item | Frozen decision |
|---|---|
| Decision Checkpoint | First eligible non-terminal ActionStep Observation Batch |
| Capture boundary | After Memory append and before the next `model.generate()` |
| Capture execution | Synchronous; no asynchronous background snapshot |
| Label | `1 / 0 / null` |
| Max-step exhaustion | Valid budget exhaustion plus failed verifier is `0` |
| Infrastructure failure | `null` when no valid outcome is observable |
| Feature allowlist | All 21 candidates remain disabled pending B2/B3 provenance |
| F4 | Required for versioned API Workflow |
| F6 | Enabled only for API write workflows |
| F6 safety | `committed / not_committed / unknown`; no blind retry for committed/unknown |
| Maximum model calls | 10 |
| Maximum tool calls | 12 |
| Maximum cumulative Tokens | 50,000 |
| Context limit | 8,192 |
| Task wall time | 900 seconds |
| LightGBM Gate | At least 200 valid Train rows, at least 30/class, plus sample-size check |
| Multiple Checkpoints | One primary Checkpoint/task in the first formal cohort |
| Dataset target | 80 templates, 48/16/16; no formal collection in B2 |

The rc1 source-stop rule is also retained: after a valid primary capture, the source attempt is
closed as `checkpoint_captured`; only an independent restored CONTINUE may later create a label.

## Frozen scope

The following are immutable within `risk-continue-v2.0.0`:

- research estimand `P(Y_continue=0 | X_<=t)`;
- Decision Checkpoint lifecycle boundary and synchronous capture;
- CONTINUE semantics and `Y_continue` tri-state label;
- failure taxonomy and F0--F6 semantic/safety boundaries;
- feature admission/provenance rules and permanent exclusions;
- Task Attempt, Decision Checkpoint, Continue Outcome and Risk Example lineage contracts;
- template-level split isolation and one primary Checkpoint rule;
- evaluation metrics, grouped statistics, Test isolation and LightGBM Gate;
- B2, B3 and B4 acceptance criteria.

B2 implementation difficulty does not authorize changing these definitions.

## Deliberately unfrozen or unapproved items

- IDs, prompts, tool chains, fault schedule and final hash for the 40 new templates;
- any claim that the 80-template v2 catalog has been implemented or validated;
- activation of any of the 21 candidate risk features;
- remote GPU allocation, vLLM start/restart, or real-Qwen B2 validation;
- B3 full CONTINUE outcome collection, B4 smoke, B5--B8 formal collection;
- RETRY/REPLAN outcome learning, risk-model training or formal Test access;
- changes to Recovery-Bench or the local/remote responsibility boundary.

These omissions do not block local B2 engineering. They do block formal data collection or any
claim that the corresponding capability has passed.

## RC1 to formal-version differences

No research meaning, threshold, label, split rule, fault semantic or Gate changed. The formal
freeze changes only:

1. `risk-continue-v2.0.0-rc1` to `risk-continue-v2.0.0`;
2. related schema/taxonomy version markers from rc1 to formal;
3. review-candidate status markers to frozen;
4. this immutable provenance/approval record and a separate freeze report.

Historical `docs/phase2/daily_reports/b1_protocol_report.md` remains unchanged as the rc1 review
record.

## Change-control rule

Any later change to the frozen scope requires:

1. a written proposal identifying the affected estimand/schema/Gate;
2. reason and evidence that v2.0.0 is infeasible or incorrect;
3. explicit user approval before code/data changes;
4. a new protocol version and config/schema identifiers;
5. preservation of all v2.0.0 records and artifacts without overwrite;
6. invalidation/recollection rules for any affected smoke or formal data.

Implementation bug fixes that do not change serialized meaning may retain the Protocol ID but
must change code/config hashes and preserve failed runs.
