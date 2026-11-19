# Day 07 Report

## Objectives

- 为四个任务领域各实现 10 个模板，共 40 个模板。
- 每个模板确定性生成 5 个实例，共 200 个实例。
- 按模板分组固定 Train / Validation / Test 为 24 / 8 / 8 个模板和 120 / 40 / 40 个实例。
- 确保同一模板的所有实例只属于一个 split，且所有任务有确定性验证器。
- 验证相同模板配置和 seed 可重复生成相同实例。
- 只完成 Day 7，不启动远程模型或 Day 8 正式轨迹采集。

## Local Environment Work

- 扩展 `TaskSpec`，向后兼容保留 `day3_smoke`，新增 `day7_catalog` 和模板级 `DataSplit`。
- 新增严格的模板 YAML loader、任务实例生成器、catalog 统计与数据泄漏检查。
- 新增 `configs/task_templates.yaml`，显式保存 40 个模板的领域、family、split、base seed 和生成参数。
- 新增 Day 7 runner，生成可追溯 catalog、split manifest，并实际执行全部 200 个 reference task。
- 对每个任务创建隔离工作区、执行本地工具、运行确定性验证器，并 reset 后比较环境状态哈希。
- 为 catalog 和模板源分别计算 SHA-256，便于 Day 8 后续轨迹追溯到固定任务版本。

## Remote Environment Work

- 无。Day 7 只涉及本地任务模板、实例生成、工具执行和验证器。
- 未通过 SSH 访问远程服务器，未启动 vLLM，未占用共享 GPU，未产生真实模型轨迹。

## Work Completed

- Data Analysis：10 模板 / 50 实例，覆盖 `eq/gt/gte/lt/lte` 过滤及 `sum/mean/count` 聚合。
- File Retrieval：10 模板 / 50 实例，覆盖递归列举、单/多匹配搜索、大小写不敏感搜索、行号、精确读取、Unicode 和多行内容。
- API Workflow：10 模板 / 50 实例，覆盖库存查询、多个订单状态查询，以及 `pending -> processing`、`processing -> shipped`、`pending -> cancelled` 安全更新。
- Calculation & Logic：10 模板 / 50 实例，覆盖加减乘除、括号、幂、单位转换及容差验证。
- 每个领域严格分配 6 Train / 2 Validation / 2 Test 模板，对应 30 / 10 / 10 实例。
- 每个领域使用到的所有工具 family 均在 Train split 中出现，避免把某一完整工具类型只留给 Validation/Test。
- 200 个 seed、task ID 和 instance ID 均唯一。
- 同一配置连续生成两次得到完全相同的任务序列和内容。
- 所有实例均保存非空 reference steps 和 expected checks，并由现有 `DeterministicVerifier` 实际验证。
- 原 Day 3 的 8 个 smoke task 及其 loader 行为保持兼容。

## Files Changed

- `configs/task_templates.yaml`
- `src/risk_agent/benchmark/schema.py`
- `src/risk_agent/benchmark/task_loader.py`
- `src/risk_agent/benchmark/template_loader.py`
- `src/risk_agent/benchmark/task_generator.py`
- `src/risk_agent/benchmark/runner.py`
- `src/risk_agent/benchmark/__init__.py`
- `scripts/run_day7_tasks.py`
- `tests/test_day7_catalog.py`
- `docs/daily_reports/day_07_report.md`

未修改 Recovery-Bench、故障类型、动作语义、Checkpoint / Restore 协议、评价指标或本地/远程职责边界。

## Artifacts Generated

最终验收 run：`runs/day07/day7_20261118_033944_4b4bdfa9/`。

- 完整模板与实例目录：`task_catalog.json`。
- 固定模板/实例划分：`split_manifest.json`。
- 200 个 reference run 和验证结果：`task_results.json`。
- 模板源 SHA-256：记录于上述三个产物中。
- Catalog SHA-256：`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`。
- `task_catalog.json` 明确标记 `formal_trajectory_data=false`；它是任务目录，不是 Day 8 真实模型轨迹或 Day 9 效用数据。
- 调整 split family 覆盖前的早期验收 run 依照数据保护规则保留，最终权威版本以上述 run ID 和 catalog hash 为准。

`runs/` 已被 `.gitignore` 排除；可复现源由版本化的模板 YAML 和生成器组成。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_day7_catalog.py
.venv/bin/python scripts/run_day7_tasks.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另使用只读 Python 检查：

- manifest 中每个 template ID 只出现于一个 split；
- catalog 中 200 个任务的 split 与 manifest 一致；
- 200 个 seed、task ID 唯一；
- 200 个执行结果均通过且 reset 哈希一致；
- 产物不含 `api_key`、`authorization` 或 `private_key` 字段。

## Test Results

- Day 7 专项测试：3 passed。
- 全量回归：47 passed。
- 依赖：`No broken requirements found.`
- Python 编译检查：通过。
- 模板：40/40；每领域 10。
- 实例：200/200；每领域 50。
- Template split：Train 24 / Validation 8 / Test 8。
- Instance split：Train 120 / Validation 40 / Test 40。
- 每领域 template split：6 / 2 / 2。
- 每领域 instance split：30 / 10 / 10。
- Reference execution：200 passed / 0 failed。
- Deterministic verifier：200/200 有检查且通过。
- Reset reproducibility：200/200 初始与 reset 后环境哈希一致。
- Generator reproducibility：通过。
- Template split isolation：40/40 模板未跨 split。
- Artifact sensitive-field scan：未发现敏感字段。

## Acceptance Checklist

- [x] 四个领域各 10 个模板。
- [x] 共 40 个唯一模板。
- [x] 每模板 5 个实例。
- [x] 共 200 个唯一实例。
- [x] 模板级 Train 24 / Validation 8 / Test 8。
- [x] 实例级 Train 120 / Validation 40 / Test 40。
- [x] 每领域执行 6 / 2 / 2 模板划分。
- [x] 同一模板全部实例位于同一 split。
- [x] 每个任务有 reference steps 和确定性 expected checks。
- [x] 200 个任务全部实际执行并通过确定性验证。
- [x] 200 个任务 reset 后状态可复现。
- [x] seed 显式记录且生成可重复。
- [x] Catalog 和模板源具有 SHA-256 版本标识。
- [x] 原 Day 3 smoke task 保持兼容。
- [x] 未提前执行 Day 8。

## Known Issues

- 当前 catalog 使用确定性合成 fixture 和本地 Mock API，不代表真实业务数据分布。
- Day 7 验收使用 reference steps，证明工具、环境和验证器可以完成任务；它不代表 Qwen3-8B 能自主规划并完成 200 个任务。
- 多数模板的最短 reference path 为一次工具调用；部分计算表达式内部含多步运算，API 写操作具有状态和幂等约束。真实 Agent 的多步轨迹复杂度需在 Day 8 实测后评估。
- 本日没有故障注入、Checkpoint 选择或三动作分支；这些任务目录将在后续日程中与 Day 4--6 模块组合。
- 模板 split 已固定在 `configs/task_templates.yaml`；Day 8 之后不得根据测试表现重新分配模板，否则会造成测试集污染。

## Remaining Work

- Day 7 核心验收无遗留阻塞项。
- Day 8 开始前应固定并记录 catalog hash `8a76a5c2...9d775`，然后重新检查远程双 Worker/GPU 可用性；未经用户继续指令不启动远程服务。
- Day 8 需要区分模型/API 基础设施失败与任务失败，并让本地 Agent 对 catalog 产生真实模型轨迹。

## Status: PASS

40 个模板、200 个实例、模板级 24/8/8 划分、确定性生成、200/200 本地执行验证和 reset 复现全部通过，满足 Day 7 核心验收。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
