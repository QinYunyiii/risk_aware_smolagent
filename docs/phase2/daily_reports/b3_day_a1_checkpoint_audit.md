# B3 Day A1 Checkpoint Audit

## Objectives

- Audit the fixed B2 Day 5 five-Checkpoint run as immutable B3 input.
- Execute `CheckpointManager.verify_v2()` for every Checkpoint.
- Record schema, identity, budget continuity, six component Hashes, Manifest Hashes and complete
  file inventories without restoring into or writing under the source Checkpoint directories.

## Source

- Source Run: `b2_day5_five_gate_20261123_001`
- Source path: `runs/phase2_b2_day5/b2_day5_five_gate_20261123_001/`
- B2 Gate: `PASS`, 5/5.
- Controller: `qwen3_model`.
- Execution mode: `model_backed_smoke`.
- Formal experiment: false.
- Research dataset eligible: false.

The required `run_manifest.json`, `checkpoint_index.jsonl`, `source_attempts.jsonl`,
`restore_audit.jsonl`, `model_request_audit.jsonl`, `budget_audit.jsonl` and
`gate_summary.json` all exist. Task IDs, Task Run IDs and Checkpoint IDs are each unique.

## Checkpoint Verification

| Task | Worker | Checkpoint | Artifacts | Manifest SHA-256 | Six Hashes |
|---|---|---|---:|---|---|
| `data_filter_category_eq_001` | worker-0 | `checkpoint_data_filter_category_eq_001_001` | 28 | `6758c96a24d688c0...e27f7836` | PASS |
| `file_list_project_tree_001` | worker-1 | `checkpoint_file_list_project_tree_001_001` | 30 | `9ca8fc2fa0073683...4828fd1` | PASS |
| `api_inventory_primary_001` | worker-0 | `checkpoint_api_inventory_primary_001_001` | 27 | `dd063c7f1b9dea77...1b52ac7` | PASS |
| `logic_calculate_addition_001` | worker-1 | `checkpoint_logic_calculate_addition_001_001` | 27 | `93f5e83e9716ff8f...217b309` | PASS |
| `data_filter_amount_gt_001` | worker-0 | `checkpoint_data_filter_amount_gt_001_001` | 28 | `4b2f7fa164d8fa99...f96f953` | PASS |

For every Checkpoint:

- schema version is `2.0.0`;
- completed step is 1 and next step is 2;
- restored Model Call usage starts from 1, not zero;
- restored Tool Call usage starts from 1, not zero;
- Agent State, Next Model Input, Environment, Runtime, Identity Config and Combined State Hash
  match both the Checkpoint Manifest and `checkpoint_index.jsonl`;
- every listed Artifact size and SHA-256 passes verification;
- the Checkpoint path resolves below the fixed source Run's `checkpoints/` directory.

## Verifier Probes

Each Checkpoint was independently restored into a temporary local workspace. For all five tasks,
the frozen deterministic Verifier passed the known positive probe and rejected a deliberately
incorrect candidate. The API Workflow probe additionally passed the frozen inventory database
check. These are offline implementation probes, not B3 labels and not research data.

## Artifacts

The complete, machine-readable inventory and all six full Hashes are stored at:

`runs/phase2_b3_continue_gate/b3_stage_a_20261123_001/checkpoint_audit.json`

The local preflight records `network_requests_made=0` and refuses output overwrite.

## Safety and Immutability

- SSH operations: 0.
- HTTPS/model requests: 0.
- GPU queries or changes: 0.
- Docker operations: 0.
- B2 Checkpoints recreated: 0.
- B2 source files modified: 0.

## Status: PASS

All five fixed B2 Checkpoints are complete, readable and compatible with the B3 Stage A
implementation. This report does not authorize B3 Stage B, B4, formal collection, or use of any
future B3 smoke label for training.
