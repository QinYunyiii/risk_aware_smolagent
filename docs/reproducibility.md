# Reproducibility Guide

## 1. Scope

This guide reproduces the current engineering prototype and its local artifacts. It does not turn the Train-only reference experiments into independent Validation/Test evidence. Commands that start remote vLLM workers require explicit authorization and a fresh shared-GPU availability check.

## 2. Local Environment

Expected environment:

- Windows host with WSL Ubuntu 24.04;
- Python 3.12;
- project-local `.venv`;
- Docker only for Recovery-Bench/Harbor tasks;
- no system Conda requirement.

Create the environment without changing system Python:

```bash
cd /home/three/projects/risk-aware-smolagents
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip check
```

`requirements.lock` pins the resolved Python packages. Model weights, run outputs, checkpoints, secrets and `.venv` are ignored by Git.

## 3. Configuration and Secrets

- Copy `.env.example` to a private `.env` only when remote inference is needed.
- Keep API keys in environment variables or private runtime configuration.
- Keep TLS verification enabled. Do not use `verify=False` or `curl -k` as an experiment path.
- Remote endpoints listen on loopback and are reached through an SSH tunnel.
- Before every remote run, select only GPUs that are both technically idle and available under laboratory policy.

The checked-in YAML files contain experiment settings, model metadata paths and non-secret endpoint templates. Machine-specific Recovery-Bench paths are in `configs/day14_recovery_bench.yaml` and must be updated when the external repository is located elsewhere.

## 4. Verification

Run the complete local suite:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

The final Day 14 environment reports 76 passing tests. A different count means the source tree or test set differs from the documented delivery.

## 5. Local Pipeline

The following commands create new append-only run directories. They do not overwrite the recorded final runs.

```bash
# Four domains and deterministic reference execution
.venv/bin/python scripts/run_day3_tasks.py

# Fault injection
.venv/bin/python scripts/run_day4_faults.py

# Checkpoint / Restore
.venv/bin/python scripts/run_day5_checkpoint.py

# Isolated Continue / Retry / Replan branches
.venv/bin/python scripts/run_day6_branches.py

# 40 templates, 200 instances, grouped split
.venv/bin/python scripts/run_day7_tasks.py

# Intervention dataset from valid checkpoints
.venv/bin/python scripts/run_day9_interventions.py

# Train-only grouped OOF risk and utility models
.venv/bin/python scripts/train_day10_risk.py
.venv/bin/python scripts/train_day11_utility.py

# Adaptive policy and Day 13 comparisons
.venv/bin/python scripts/run_day12_policy.py
.venv/bin/python scripts/run_day13_experiment.py --smoke
.venv/bin/python scripts/run_day13_experiment.py
```

Day 9–13 configs intentionally reference the recorded upstream run IDs. To reproduce from newly generated upstream data, copy the config, change only the source artifact paths and preserve the catalog hash, split and research settings. Changing labels, actions, thresholds, utility weights or split semantics creates a different experiment and must be documented as such.

## 6. Remote Qwen3-8B Runs

Day 2 and Day 8 require two independently authorized, currently idle RTX 4090 GPUs. The management script is idempotent: inspect status before deciding whether a service must start.

```bash
.venv/bin/python scripts/manage_remote_vllm.py status
.venv/bin/python scripts/manage_remote_vllm.py start
.venv/bin/python scripts/manage_remote_vllm.py wait-health
.venv/bin/python scripts/manage_remote_vllm.py tunnel-start

.venv/bin/python scripts/check_remote_api.py
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/run_day8_collection.py --smoke
.venv/bin/python scripts/run_day8_collection.py

.venv/bin/python scripts/manage_remote_vllm.py tunnel-stop
.venv/bin/python scripts/manage_remote_vllm.py stop
.venv/bin/python scripts/manage_remote_vllm.py status
```

Do not start workers when either selected GPU is in use. Do not terminate unrecognized processes. Always confirm that this run's PIDs, tunnels and GPU memory have been released.

When the laboratory has explicitly reserved two physical GPUs for the run, pin them without changing the default dynamic policy:

```bash
.venv/bin/python scripts/manage_remote_vllm.py start --gpu-ids 0,1
```

The command still requires both requested GPUs to pass two idle samples and the compute-process check. It fails instead of silently selecting other cards.

## 7. Recovery-Bench Read-Only Demo

Prerequisites for the recorded machine:

- Recovery-Bench at `/home/three/projects/agent-intervention/recovery-bench`;
- Git HEAD `c5f83f2b`;
- Harbor 0.4.0 in the parent project's `.venv`;
- the selected historical trial artifacts from the configured run.

Run:

```bash
.venv/bin/python scripts/run_day14_recovery_bench_demo.py
```

The demo:

- reads three existing Harbor trials;
- validates task checksums, config/result equality, ATIF version and verifier reward files;
- exports only metadata, hashes and redacted normalized calls/observations;
- verifies source artifact SHA-256 values before and after parsing;
- explicitly records that Checkpoint/Restore and branch utility are unsupported for these Harbor Docker artifacts.

It does not start Docker, rerun a benchmark, invoke a model, replay commands, or modify Recovery-Bench.

## 8. Recorded Final Artifacts

| Stage | Recorded run |
|---|---|
| Day 7 catalog | `runs/day07/day7_20261118_033944_4b4bdfa9` |
| Day 8 initial trajectories | `runs/day08/day8_20261119_074032_7ec4c41e` |
| Day 9 branches | `runs/day09/day9_20261119_080843_288841fc` |
| Day 10 risk | `runs/day10/day10_20261119_082058_aab1d057` |
| Day 11 utility | `runs/day11/day11_20261119_082900_e8d013ce` |
| Day 12 policy | `runs/day12/day12_20261119_083943_b18d954e` |
| Day 13 comparison | `runs/day13/day13_20261119_085002_5754c690` |
| Day 14 Recovery-Bench | `runs/day14/day14_20261119_090131_2c077c82` |

Run directories are ignored by Git and must be archived separately if the repository is moved. Every formal summary records its upstream IDs and hashes.

## 9. Interpretation Rules

- Day 3–7 smoke/reference results prove implementation behavior, not LLM autonomy.
- Day 8 is the real Qwen3-8B collection stage; infrastructure failures remain separate from task failures.
- Day 10–13 metrics use only 20 Train checkpoints derived from local reference scenarios. They are not official Test metrics.
- Retry/Replan outcome data have sparse coverage and no negative examples in the current applicable samples.
- Recovery-Bench command replay is not state restore. Do not compute same-state branch utility from it.
- Never use Test data to choose thresholds, features, utility weights or hyperparameters.
