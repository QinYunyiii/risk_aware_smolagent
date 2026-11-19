# Demo

## Local Delivery Demo

Run the full test suite and the final read-only Recovery-Bench integration:

```bash
cd /home/three/projects/risk-aware-smolagents
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_day14_recovery_bench_demo.py
```

Expected Day 14 behavior:

- three selected Harbor 0.4.0 trials;
- one `success`, one `verifier_failure`, one `agent_error`;
- ATIF schema `ATIF-v1.6`;
- source artifact hashes unchanged;
- raw historical arguments and observations omitted from new artifacts;
- `checkpoint_restore_supported=false`;
- `branch_utility_experiment_executed=false`.

Recorded final output:

```text
runs/day14/day14_20261119_090131_2c077c82/
├── recovery_bench_demo_summary.json
├── selected_trials.jsonl
├── normalized_tool_calls.jsonl
├── normalized_observations.jsonl
└── source_artifact_manifest.json
```

The selected sample contains 37 ATIF steps, 100 tool calls and 34 observation results. Twenty-three observation results lack a reliable call-level link and remain explicitly `step_level_unlinked`; the Adapter does not invent an association.

## Inspect the Summary

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

path = Path(
    "runs/day14/day14_20261119_090131_2c077c82/"
    "recovery_bench_demo_summary.json"
)
summary = json.loads(path.read_text(encoding="utf-8"))
print(summary["passed"])
print(summary["status_counts"])
print(summary["checks"])
PY
```

## Optional Raw Inspection

`RecoveryBenchAdapter.load_trial(..., include_content=True)` can expose source arguments and observation contents to an authorized caller in memory. The formal Demo never enables this mode and never copies raw historical content into the project run directory.

## What This Demo Does Not Prove

- It does not rerun the original agent or verifier.
- It does not demonstrate a Harbor checkpoint or exact restore.
- It does not create Continue/Retry/Replan branches.
- It does not use the three Recovery-Bench trials as utility-training examples.
- It does not contact the remote Qwen3-8B service or occupy a GPU.

For the complete local/remote workflow, see [reproducibility.md](reproducibility.md). For findings and limitations, see [final_report.md](final_report.md).
