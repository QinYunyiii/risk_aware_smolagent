# Day 09 Report

## Objectives

- 从 Day 8 非基础设施失败任务构造可恢复的决策时刻 Checkpoint。
- 对每个有效 Checkpoint 执行 `CONTINUE`，并执行安全且适用的 `RETRY` / `REPLAN`。
- 每个候选动作从独立 Restore 工作区开始，记录适用性、确定性验证结果和增量成本。
- 明确排除 Day 8 基础设施失败，不通过重复运行把真实失败改写为成功。
- 保持同一 Checkpoint 的所有动作分支位于同一 Split。
- 只完成 Day 9，不训练 Day 10 风险模型。

## Local Environment Work

- 以 Day 8 正式逻辑结果 `day8_20261119_074032_7ec4c41e` 为来源，核验固定 catalog SHA-256：`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`。
- 24 个 Day 8 逻辑任务中，排除 4 个 `infra_failure`；保留 19 个 `success` 和 1 个真实 `task_failure`，共生成 20 个 Checkpoint。
- Day 8 没有保存可直接恢复的逐步 Checkpoint，因此本日没有伪称恢复 Day 8 运行内存；而是用同一任务、seed、初始环境、决策时刻 Token 和来源 ID 构造新的 pre-tool Checkpoint。
- Checkpoint 只保存决策时刻可知的 prompt、pending operation、失败元数据、预算和本地环境；不写入 Day 8 最终状态或未来分支结果。
- 为 20 个 Checkpoint 分配 5 个 no-fault、5 个 transient service unavailable、5 个 invalid argument、4 个 stale observation 和 1 个 partial-failure-not-committed 场景。
- 每个动作均先独立 Restore；不适用动作仍保留结构化记录和原因，但不执行工具。
- 扩展动作适用性规则：只读操作允许在明确安全的失败状态下 Retry；写操作仍要求非空幂等键；Replan 要求存在明确的修正步骤或状态刷新操作。
- 使用本地确定性 reference controller 执行分支并由 `DeterministicVerifier` 判断结果。额外模型 Token 实测为 0，未填充合成 Token。

## Remote Environment Work

- 无。Day 9 分支恢复、工具执行和验证全部位于本地 WSL。
- 未启动 SSH 隧道、远程 vLLM 或 Qwen3-8B，未占用共享 GPU。
- 产物显式记录 `formal_remote_model_branches=false` 和 `controller_type=deterministic_local_reference`。

## Work Completed

- 新增 Day 9 scenario、Checkpoint preparation 和 branch outcome schema。
- 新增通用 `InterventionBranchRunner`，逐 Checkpoint 生成 `CONTINUE`、`RETRY`、`REPLAN` 三条动作记录。
- 完成 20 个 Checkpoint、60 条候选动作记录：
  - `CONTINUE`：20/20 适用并执行；
  - `RETRY`：6/20 适用并执行，14/20 明确记录不适用原因；
  - `REPLAN`：10/20 适用并执行，10/20 明确记录不适用原因。
- 共 36 个适用动作全部从独立 Restore 执行；24 个不适用动作均有原因。
- 36 个适用分支中 27 个通过确定性验证；9 个失败均为 invalid/stale 场景的 `CONTINUE` 对照结果，按真实结果保留，未重跑至成功。
- 所有 60 条记录包含 Token、工具调用数、延迟、终止原因、Checkpoint hash、Split 和适用性字段。
- 20 个 Checkpoint 及全部 60 条动作记录均为 Train split，同一 Checkpoint 没有跨 Split。
- 生成 eligibility manifest、Checkpoint manifest、append-only branch outcomes、初始 utility dataset 和验收 summary。

## Files Changed

- `configs/day9_interventions.yaml`
- `src/risk_agent/branching/applicability.py`
- `src/risk_agent/intervention/__init__.py`
- `src/risk_agent/intervention/schema.py`
- `src/risk_agent/intervention/scenarios.py`
- `src/risk_agent/intervention/runner.py`
- `scripts/run_day9_interventions.py`
- `tests/test_day9_interventions.py`
- `docs/daily_reports/day_09_report.md`

未修改 Recovery-Bench、Day 7 catalog/split、Day 8 原始轨迹、研究效用公式或本地/远程职责边界。

## Artifacts Generated

正式 run：`runs/day09/day9_20261119_080843_288841fc/`

- `eligibility_manifest.json`：20 个有效来源和 4 个被排除的基础设施失败。
- `checkpoint_manifest.json`：20 个 Checkpoint、来源 task run、场景、Split 和状态 hash。
- `checkpoints/`：文件、SQLite、Mock API、RuntimeState 和任务配置的真实快照。
- `branches/`：36 个适用动作的独立 Restore 工作区和工具轨迹；不适用动作不执行工具。
- `branch_outcomes.jsonl`：60 条候选动作结果，只追加写入。
- `utility_dataset.json`：Day 9 初始干预数据，明确标记为非远程模型分支。
- `intervention_summary.json`：统计、产物路径和全部验收 Gate。

`runs/` 由 `.gitignore` 排除，原始与派生产物保留在本地而不进入 Git。
配置守卫完善前的预检 run `day9_20261119_080533_59091e36` 也按只追加原则保留，最终验收数字取上述正式 run。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_day9_interventions.py
.venv/bin/python scripts/run_day9_interventions.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另以只读审计脚本复核 action/applicability/success 组合、来源状态、唯一 branch ID、Checkpoint/source 链接、Split、成本、失败原因和敏感字段。

## Test Results

- Day 9 专项测试：4 passed。
- 全量回归：54 passed。
- 依赖：`No broken requirements found.`
- Python 编译：通过。
- 来源：24 个 Day 8 逻辑任务；20 个非 infra 来源入选，4 个 infra 来源排除。
- Checkpoint：20；唯一状态 hash 20/20；唯一 Day 8 source task run 20/20。
- 候选动作记录：60；唯一 branch ID 60/60。
- 适用动作：36；全部执行、全部有确定性验证结果、全部 Restore hash 与 Checkpoint hash 一致。
- 不适用动作：24；全部记录明确原因和零增量成本。
- 适用动作成功：27；失败对照：9。
- 工具调用增量：37 次；`CONTINUE=20`、`RETRY=6`、`REPLAN=11`。
- 额外模型 Token：60/60 为 0，符合本地 reference controller 实测值。
- 额外延迟范围：0 至 0.002929 秒。
- Split：20/20 Checkpoint 和 60/60 动作记录均为 Train；无跨 Split。
- 敏感信息扫描：未发现 API Key、Authorization、Bearer、密码或私钥内容。
- `intervention_summary.json`：全部 Gate 为 true，`passed=true`。

## Acceptance Checklist

- [x] 只选择 Day 8 非基础设施失败来源。
- [x] 每个有效 Checkpoint 都有 Continue 结果。
- [x] Retry 仅在安全且适用时执行。
- [x] Replan 仅在存在修正计划或状态刷新步骤时执行。
- [x] 所有不适用动作均记录原因。
- [x] 每个适用动作从独立 Restore 工作区执行。
- [x] Restore 后状态 hash 与 Checkpoint hash 一致。
- [x] 所有适用分支均由确定性验证器判定。
- [x] Token、工具调用和延迟成本字段完整且非负。
- [x] 同一 Checkpoint 的分支不跨 Split。
- [x] 真实失败分支未被重复运行至成功。
- [x] 基础设施失败未标成 Agent 失败。
- [x] 数据明确区分本地 reference controller 与远程模型分支。
- [x] 未提前开始 Day 10。

## Known Issues

- Day 8 当时没有捕获可直接恢复的逐步 Checkpoint。本日 Checkpoint 是与 Day 8 task/run 可追溯关联的 fresh pre-tool snapshots，不是 Day 8 进程内状态的事后恢复。
- 场景和修正步骤由本地确定性 reference controller 构造；这批数据适合验证分支协议和形成初始干预数据，不代表 Qwen3-8B 在同状态下自主生成的动作分布。
- 20 个来源全部属于 Train split。没有为了覆盖 Validation/Test 而改变 Day 7 split 或跨 split 复制 Checkpoint。
- 动作覆盖不均衡：Continue 20、Retry 6、Replan 10。后续效用建模必须报告 coverage，且不得把不适用动作当作失败标签。
- 本地 reference controller 没有额外模型 Token；当前 latency 只表示本地工具执行，不代表远程 vLLM 延迟。
- `utility_dataset.json` 是初始干预数据。若后续研究结论要求真实模型生成分支，应在既定协议下另行采集并作为不同 controller/source 分层分析，不能覆盖本批产物。

## Remaining Work

- Day 9 核心工程验收无阻塞项。
- Day 10 开始前保留 catalog hash 和 template-level split，风险特征只能使用决策时刻已知信息；不得把 Day 9 分支结果或未来成本作为风险特征。
- Day 11 使用本批数据前应显式处理动作 coverage、不适用动作和本地 reference controller 限制。
- 等待用户确认后才进入 Day 10。

## Status: PASS

20 个有效 Checkpoint 全部具有 Continue 结果，安全适用的 Retry/Replan 均从独立 Restore 执行；不适用原因、成本、验证结果和 Split 均完整，全部 Day 9 Gate 通过。上述 PASS 指 Day 9 指南的工程验收，不把本地 reference controller 数据表述为远程模型因果效应证据。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
