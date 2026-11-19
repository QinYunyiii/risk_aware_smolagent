# P1-D1 历史实验与产物基线审计

## 审计范围与边界

- 审计日期：2026-11-19（Asia/Shanghai）。
- 项目根目录：`/home/three/projects/risk-aware-smolagents`。
- 范围：Day 7--14 的正式 run、最终模型、对应配置、summary、日报及其来源关系。
- 方法：只读解析 JSON/JSONL、检查标识符和必需字段、逐文件 SHA-256、正式目录全树 SHA-256。
- 未执行：未重跑历史实验，未访问或修改外部 Recovery-Bench，未访问 Docker、SSH、GPU 或远程模型服务。
- 研究含义：本审计仅证明产物可定位、结构一致和后续可复核，不构成新的实验结论。

审计开始时 `git status --short` 显示仓库内容均为未跟踪文件，包括
`runs/`、`models/`、`docs/`、`src/` 和 `tests/`。因此当前不能用 Git commit
证明历史产物来源；本次生成的 SHA-256 基线是后续完整性检查的权威本地依据。

## 正式产物清单

| Day | 正式 run / 模型 | 配置 | Summary | 关键数据产物 |
|---|---|---|---|---|
| 7 | `runs/day07/day7_20261118_033944_4b4bdfa9` | `configs/task_templates.yaml`, `configs/tasks.yaml` | `task_results.json` | `task_catalog.json`, `split_manifest.json`, `environments/` |
| 8 | `runs/day08/day8_20261119_074032_7ec4c41e` | `configs/day8_collection.yaml` | `collection_summary.json` | `logical_task_results.json`, `task_trajectories.jsonl`, `tool_trajectories.jsonl`, `preflight.json` |
| 9 | `runs/day09/day9_20261119_080843_288841fc` | `configs/day9_interventions.yaml` | `intervention_summary.json` | `checkpoint_manifest.json`, `branch_outcomes.jsonl`, `utility_dataset.json`, `checkpoints/`, `branches/` |
| 10 | `runs/day10/day10_20261119_082058_aab1d057`; `models/risk/day10_20261119_082058_aab1d057` | `configs/day10_risk.yaml` | `training_summary.json` | `risk_dataset.json`, `leakage_audit.json`, OOF 预测、指标、2 个最终模型与 metadata |
| 11 | `runs/day11/day11_20261119_082900_e8d013ce`; `models/utility/day11_20261119_082900_e8d013ce` | `configs/day11_utility.yaml` | `training_summary.json` | `utility_training_dataset.json`, coverage/OOF/metrics、12 个最终模型与 metadata |
| 12 | `runs/day12/day12_20261119_083943_b18d954e` | `configs/day12_policy.yaml` | `policy_summary.json` | `policy_decisions.jsonl`, `policy_e2e_records.json`, `selected_branch_outcomes.jsonl` |
| 13 | `runs/day13/day13_20261119_085002_5754c690` | `configs/day13_experiment.yaml` | `main_experiment_summary.json` | `policy_task_results.jsonl`, `selected_branch_outcomes.jsonl`, metrics、paired comparisons |
| 14 | `runs/day14/day14_20261119_090131_2c077c82` | `configs/day14_recovery_bench.yaml` | `recovery_bench_demo_summary.json` | `selected_trials.jsonl`, normalized calls/observations、source manifest |

Day 7--13 的 summary 均声明相同的 catalog SHA-256：
`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`，
且各自 `passed=true`。Day 14 是独立的 Recovery-Bench 只读适配演示，没有
`catalog_sha256` 字段；它的 summary 同样为 `passed=true`。这些历史标志仅作为
产物事实记录，本审计仍独立核对了关键计数和引用关系。

## 来源关系

```text
Day 7 catalog/splits
  -> Day 8 real-model attempts and 24 logical results
      -> Day 9 20 checkpoint states and 60 candidate actions
          -> Day 10 risk dataset/models (20 checkpoints, 26 features)
          -> Day 11 utility dataset/models (60 actions, 26 features)
              -> Day 12 full-policy decisions (20 records)
                  -> Day 13 9 policies x 20 checkpoints (180 records)

Day 14 Recovery-Bench read-only source artifacts -> normalized adapter demo
```

Day 10 和 Day 11 的 `model_metadata.json` 都将训练来源明确记录为
`runs/day09/day9_20261119_080843_288841fc`。两者各有 26 个互异特征，特征名及顺序
完全一致。

## 标识符、字段与计数审计

| 对象 | 实际结果 | 判定 |
|---|---|---|
| Day 7 task | 200 条，200 个唯一 `task_id`；Train/Validation/Test = 120/40/40 | 通过 |
| Day 8 logical task | 24 条，24 个唯一 `task_id` | 通过 |
| Day 8 attempts | 43 条，43 个唯一 `task_run_id`；19 个 task 各 2 次、5 个各 1 次 | 重试是预期重复，未发现 attempt ID 冲突 |
| Day 8 status | success 19、infra_failure 4、task_failure 1 | 与 24 个 logical task 一致 |
| Day 9 checkpoints | 20 条，20 个唯一 `checkpoint_id` | 通过 |
| Day 9 action records | 60 条，60 个唯一 `branch_id` 和 60 个唯一 `(checkpoint_id, action)` | 通过 |
| Day 9 applicability | CONTINUE 20/20；RETRY 6/20；REPLAN 10/20；总计 36/60 applicable | 通过 |
| Day 10 risk examples | 20 条；模型 metadata 26 个唯一特征 | 通过 |
| Day 11 utility examples | 60 条，其中 36 条 applicable；metadata 26 个唯一特征 | 通过 |
| Day 12 policy records | 20 条；decision/execution 的 split 完整，execution controller 完整 | 通过 |
| Day 13 policy/results | 180/180；9 个 policy 各 20 条 | 通过 |
| Day 13 correspondence | 180 个唯一 `(policy, checkpoint)`；branch ID 集合相同；0 个 payload mismatch | 一一对应通过 |
| Day 14 trials | 3 条；0 条具备 branch utility eligibility | 记录为适配演示，不纳入 Day 9--13 分支实验链 |

适用数据集中的 `split` 均完整。Day 9、12、13 的分支执行记录均有
`controller_type`。发现两个需要保留的 schema 观察项：

1. Day 8 的 43 条模型 attempt 均没有 `controller_type`；该阶段早于分支控制器
   schema，不能据此推断其 controller 类型。
2. Day 9 的 20 条 Checkpoint 记录没有单独名为 `checkpoint_hash` 的字段，但全部有
   非空 `state_hash`。后续代码若要求 `checkpoint_hash`，必须显式做 schema adapter，
   不能静默假设字段存在。

未发现缺失 split、重复 logical task ID、重复 attempt ID、重复 Checkpoint ID、重复
branch ID、未知 Checkpoint 引用，或 Day 13 policy/result 对应错误。

## SHA-256 基线

`historical_artifact_manifest.json` 记录了 80 个关键文件的路径、字节数和逐文件
SHA-256；同时对 10 个正式 run/model 目录内的全部 1,837 个文件计算确定性树摘要。
树摘要算法是按相对路径排序后，对
`relative_path + NUL + size + NUL + file_sha256 + LF` 进行 SHA-256。

| 历史目录 | 文件数 | 字节数 | tree SHA-256 |
|---|---:|---:|---|
| Day 7 run | 388 | 6,116,783 | `6e0be9f8dcf9958b9c2c96083d226867c71b197247bc152999cb0a34931b8b18` |
| Day 8 run | 51 | 1,131,593 | `51617d377525dd689a4fed099e2fc72cf648d899bccd4a1b61cb8d4d8e6cc78a` |
| Day 9 run | 326 | 3,227,785 | `c0f07f51cfa867b6896348dc4dab1ab11fb63efe640114040fb30e4c06f6c1e4` |
| Day 10 run | 7 | 55,674 | `960e7eb31293bbdc77de8fbf49aa1f6d7cd32459d92e83fd931790d0ca6070b8` |
| Day 10 models | 3 | 38,175 | `a0616bff7e66c850e3156338f59d712b8a3dd8376593005935295e39ad363af5` |
| Day 11 run | 8 | 153,386 | `cc14130e4b9624d30b341869705860a7e7f80da2a1bbc5effc5567282b873eb7` |
| Day 11 models | 13 | 15,589 | `85dda4f1e7550536b1e484e973610c2b2bdf40ea00db9255e9eb286c5abea70c` |
| Day 12 run | 113 | 1,486,642 | `f72f067c6f4a1374639c777db5358031e1d92de228a29194deffea0dd6809da2` |
| Day 13 run | 923 | 11,624,335 | `51497042d91928337a5cca526919806030d8e5e6b1a7112b92e2b22c31fac86a` |
| Day 14 run | 5 | 145,888 | `702715e868c6e7ec9d3ff00e739a533ccc5b054dfd5a06c5d18536d1c3afc357` |

复核命令：

```bash
.venv/bin/python scripts/audit_phase2_history.py --check-only
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_phase2_history_audit.py
```

审计脚本的默认写入模式拒绝覆盖已有 manifest；复核应使用 `--check-only`。任何
未来基线变更都应先解释原因，不应使用 `--force` 覆盖本清单。

## Gate 结论与遗留风险

- Day 7--14 的每个正式 run、Day 10/11 最终模型、配置、summary 和日报均可定位。
- 关键文件 SHA-256 与正式目录全树 SHA-256 可通过脚本复核。
- 缺失/别名字段已逐项记录；没有对旧产物做 schema 回填。
- 审计前后正式目录树摘要一致，未修改历史实验产物。
- 主要遗留风险是整个仓库尚无 Git 跟踪/commit，当前完整性依赖本地 manifest；是否
  建立 Git 基线属于后续独立决策，不在 P1-D1 中执行。

P1-D1 Gate：**PASS**。
