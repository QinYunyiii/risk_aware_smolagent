# Risk Data Schema v2

> Schema family: `risk-data-v2.0.0`
> Raw storage: append-only JSONL; derived records never overwrite source records.

## 1. Common rules

All records contain `record_type`, `schema_version`, `experiment_id`, `run_id`, `split`,
`created_at`, `record_sha256`, and versioned model/Prompt/tool/verifier/catalog metadata where
applicable. IDs are immutable and globally unique within the experiment. References use IDs and
relative artifact paths; absolute machine paths and secrets are prohibited.

`record_sha256` is SHA-256 of canonical JSON after omitting only `record_sha256` itself. Artifact
records separately store path, byte size and SHA-256. JSONL files are opened append-only with
fsync; corrections append a superseding record with `supersedes_record_id` and reason.

### 1.1 Missingness and zero

Every cost/usage measure is represented as:

```json
{"value": 0, "status": "observed"}
```

Allowed statuses:

| Status | Value | Meaning |
|---|---|---|
| `observed` | number including 0 | Measurement occurred; zero is a real zero |
| `not_occurred` | null | Event never happened, so no measurement exists |
| `not_recorded` | null | Event happened but instrumentation did not record it |
| `collection_failed` | null | Instrumentation attempted and failed |
| `not_applicable` | null | Measure is not meaningful for this record |

Missing values are never defaulted to zero. Labels use integer `1`, integer `0`, or JSON `null`.

## 2. Task Attempt

Primary key: `task_run_id`.

Required fields:

- lineage: `experiment_id`, `run_id`, `task_run_id`, `template_id`, `task_id`,
  `instance_id`, `split`, `seed`;
- execution: `controller_type`, `execution_mode`, `formal_experiment`, `worker_id`, endpoint
  alias (not secret URL if sensitive), `model_id`, `model_version`, `generation_config_hash`;
- versions: `catalog_hash`, `split_manifest_hash`, `prompt_version`, `tool_version`,
  `verifier_version`, `checkpoint_schema_version`;
- lifecycle: `started_at`, `ended_at`, `task_status`, `failure_category`,
  `termination_reason`, `side_effect_status`;
- usage: typed Prompt/Completion/total Token, model/tool call, model/tool/wall latency measures;
- integrity: source config checksum and `record_sha256`.

`task_status` values include `checkpoint_captured`, `success`, `task_failure`,
`model_service_infra_failure`, `environment_infra_failure`, `budget_terminated`, and
`manual_abort`. A source attempt closed after primary capture uses `checkpoint_captured`, not
success/failure.

## 3. Decision Checkpoint

Primary key: `checkpoint_id`. Foreign key: `task_run_id -> TaskAttempt.task_run_id`.

Required fields:

- source/task identity and split;
- `decision_trigger=first_eligible_nonterminal_tool_observation`;
- `step_index`, `feature_cutoff_event_id`, `capture_granularity`;
- model/Worker/Prompt/tool/verifier/generation metadata;
- relative references to complete messages, full memory steps, Assistant tool calls, Tool
  Observations, Agent runtime, Environment manifest and Runtime state;
- `agent_state_hash`, `next_model_input_hash`, `environment_state_hash`,
  `runtime_state_hash`, `identity_config_hash`, `combined_state_hash`;
- artifact manifest checksum and `checkpoint_schema_version=2.0.0`;
- `internal_fault_metadata_ref` in a separate audit namespace;
- `observable_features_ref` generated before CONTINUE starts.

The Checkpoint contains no outcome label, verifier result, future cost, or future event. The
machine schema is `checkpoint_schema_v2.json`.

## 4. Continue Outcome

Primary key: `continue_outcome_id`. Logical branch key: `branch_id`; execution key:
`branch_attempt_id`. Foreign key: `checkpoint_id -> DecisionCheckpoint.checkpoint_id`.

Required fields:

- `action=CONTINUE`, `applicable=true`, `controller_type=qwen3_model`;
- `source_state_hash`, restored component hashes, `first_model_input_hash`, and match booleans;
- branch workspace ID and isolation audit reference;
- `started_at`, `ended_at`, model/tool/verifier event ranges;
- `verifier_passed` (`true`/`false`/`null`), full check reference, `outcome_source`;
- `y_continue` (`1`/`0`/`null`), `failure_category`, `termination_reason`, censor reason;
- typed Prompt/Completion/total Token, model/tool call, model/tool/wall latency measures;
- model/Worker/Prompt/tool/verifier versions and checksums.

If an infrastructure attempt is safely repeated before side effects, each attempt has a new
`branch_attempt_id`; the failed attempt remains. A single pre-registered resolution record states
which attempt, if any, provides the label.

## 5. Risk Example

Primary key: `risk_example_id`. Foreign keys: `checkpoint_id` and
`continue_outcome_id`.

Required fields:

- task/template/split and cluster IDs;
- `feature_schema_version`, ordered feature names/values and `features_sha256`;
- feature provenance artifact, cutoff event/step and `future_data_excluded=true`;
- `label` (`1`/`0`; null outcomes do not enter model fitting), `label_semantics` where
  `1=continue_success` and `0=continue_failure`;
- `label_source=deterministic_verifier_plus_outcome_evaluator`;
- source Checkpoint/outcome record hashes;
- derivation code/config version and `record_sha256`.

Raw Checkpoint and outcome records are retained even when `label=null`; no fit-ready Risk Example
is emitted for them. Quality reports count all null/censored records and reasons.

## 6. Lineage

```text
TaskAttempt(task_run_id)
  -> DecisionCheckpoint(checkpoint_id, feature cutoff, state hashes)
       -> ContinueOutcome(continue_outcome_id, branch_attempt_id, verifier)
            -> RiskExample(risk_example_id, feature hash, label lineage)
```

Split equality is checked at every edge. A Test-labelled record is rejected by training loaders.
No join is allowed from internal fault metadata into the feature vector.

## 7. Timestamp policy

Timestamps are UTC ISO-8601 with an explicit offset and are used for lineage/order, not semantic
Restore equality. Monotonic durations are used for latency. Wall-clock timestamps are excluded
from state hashes unless the literal timestamp appeared in Agent-visible messages or affects
future environment behavior.

## 8. Integrity and validation

Before a record is accepted:

1. validate schema and enumerations;
2. validate foreign keys and identical split/template lineage;
3. verify artifact and source record checksums;
4. verify Checkpoint source/restored component hashes;
5. verify exact first resumed model input;
6. reject forbidden feature names/sources;
7. reject untyped zero/missing cost values;
8. reject duplicate primary keys and conflicting resolved labels.

Schema validation code is a B2/B3 implementation deliverable; B1 defines the contract only.
