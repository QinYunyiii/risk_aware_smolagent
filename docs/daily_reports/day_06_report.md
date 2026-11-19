# Day 06 Report

## Objectives

- 实现 `CONTINUE`、`RETRY`、`REPLAN` 三个候选动作的独立分支执行。
- 从同一个 Checkpoint 恢复三个完全独立的可变状态副本。
- 对至少 5 个三个动作均适用的 Checkpoint 完成三分支验收。
- 验证恢复初始哈希一致、文件/SQLite/RuntimeState 无跨分支串扰、结果由确定性验证器判定。
- 只完成 Day 6，不生成 Day 7 任务模板或正式实验数据。

## Local Environment Work

- 复用 Day 5 `CheckpointManager`，每个动作恢复到独立的工作区目录和独立 SQLite 文件。
- 新增 Branch Runner、动作适用性检查、结构化结果和本地 smoke fixture。
- `RETRY` 复用 Day 4 安全判定，仅允许已知 `not_committed` 且携带幂等键的写操作；`committed`、`unknown` 或缺失幂等键均不可直接 Retry。
- `REPLAN` 先调用 `get_order` 刷新状态，再根据观测到的状态执行带新幂等键的 `update_order`。
- 为工具轨迹增加可选分支上下文：`branch_id`、`action`、`action_applicable`。
- 每个分支记录初始/恢复/最终状态哈希、验证结果、额外 Token、工具调用数、延迟和终止原因。
- 运行动态串扰探针：在隔离副本 A 修改文件、SQLite 和 Agent Memory，确认副本 B 的组合状态哈希、文件和数据库均不变。

## Remote Environment Work

- 无。Day 6 为本地 Checkpoint / Restore / Branch Runner 验收，不需要 SSH、远程 vLLM 或 GPU。
- 未启动远程服务，未占用共享 GPU，未下载模型。

## Work Completed

- 实现 `BranchApplicabilityEvaluator`：
  - `CONTINUE` 要求存在合法 pending operation；
  - `RETRY` 要求存在失败操作、明确未提交、通过 Day 4 Retry 安全规则且写操作具有幂等键；
  - `REPLAN` 要求存在状态刷新操作。
- 实现 `BranchRunner`：
  - 对每个 Checkpoint 顺序执行三个独立恢复分支；
  - 每个分支使用独立路径、SQLite 文件和反序列化 RuntimeState；
  - 用 `DeterministicVerifier` 判定最终结果；
  - 记录实测本地工具成本，不伪造模型 Token。
- 创建 5 个独立 smoke Checkpoint。每个 Checkpoint 均保存不同的环境 marker、运行态 marker、失败事件和幂等键，但同一 Checkpoint 的三个动作始终从完全相同的快照恢复。
- 完成 15 个动作分支：`CONTINUE` 5、`RETRY` 5、`REPLAN` 5。
- 所有分支的订单最终状态均为 `shipped`、version 2，结果和数据库状态均通过确定性验证。
- 明确产物为 `day6_smoke`、`formal_experiment=false`，没有冒充 Day 9 正式效用数据。

## Files Changed

- `src/risk_agent/branching/__init__.py`
- `src/risk_agent/branching/applicability.py`
- `src/risk_agent/branching/runner.py`
- `src/risk_agent/branching/schema.py`
- `src/risk_agent/branching/smoke.py`
- `src/risk_agent/monitoring/execution_monitor.py`
- `scripts/run_day6_branches.py`
- `tests/test_branching.py`
- `docs/daily_reports/day_06_report.md`

未修改 Recovery-Bench、Day 3 任务定义、研究动作语义、远程配置或 Day 7 数据模板。

## Artifacts Generated

- 验收 run：`runs/day06/day6_20261118_031344_22c41426/`。
- 汇总：`runs/day06/day6_20261118_031344_22c41426/branch_results.json`。
- 5 个 Checkpoint：`checkpoints/checkpoint_001` 至 `checkpoint_005`。
- 15 个动作分支：每个 Checkpoint 下的 `continue`、`retry`、`replan`。
- 每个动作分支分别保存 `trajectory.jsonl` 和 `branch_result.jsonl`。
- 动态隔离探针保存在各 Checkpoint 的 `branches/.../isolation_probe/` 独立副本中。
- 5 个 Checkpoint 组合状态哈希：
  - `4effed1e5075a249b155bace9be7d611ce7d8977fc33963766045661868d8d3c`
  - `fd040784d24508d771550c00140c7e9908fa9baeb23584ec51a32a34eaa56bc4`
  - `a409907892e6994349f9aaf9805b9186df9637a159dade6838b6dff1b3431972`
  - `963dd4ed730ee82b36181031c6f3753494891a207984832f2c1a5666bf1bc559`
  - `a39677f4badeff7b8fbe7f2395ade2bde18b461ecb514f28f5a78da2ddf4230d`

`runs/` 已由 `.gitignore` 排除，原始验收产物保留在本地但不会进入 Git。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_branching.py
.venv/bin/python scripts/run_day6_branches.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另使用只读脚本复核 `branch_results.json` 的动作计数、Checkpoint 哈希、每动作工具成本、轨迹字段和敏感字段缺失情况。

## Test Results

- 新增 Day 6 测试：2 passed。
- 全量回归：44 passed。
- 依赖：`No broken requirements found.`
- Python 编译检查：通过。
- Checkpoint：5/5 通过。
- 分支：15/15 成功并通过确定性验证。
- 动作分布：`CONTINUE=5`、`RETRY=5`、`REPLAN=5`。
- 同 Checkpoint 三分支：5/5 初始组合状态哈希完全一致，恢复哈希均等于 Checkpoint manifest 哈希。
- 工作区、SQLite inode、RuntimeState 对象：每组三分支均独立。
- 动态文件/SQLite/Memory 串扰探针：5/5 通过。
- 成本：CONTINUE 和 RETRY 各 1 次本地工具调用；REPLAN 各 2 次本地工具调用。
- Token：所有分支为本地 reference runner，实测模型 Token 为 0；没有填充合成 Token。
- 轨迹字段包含 Worker、工具参数、结果、Token、延迟、Checkpoint、Branch 和 Action。
- 汇总与轨迹中未发现 `api_key`、`authorization` 或 `private_key` 字段。

## Acceptance Checklist

- [x] 实现 CONTINUE。
- [x] 实现 RETRY，并阻止已提交、未知提交状态或缺少幂等键的危险写重试。
- [x] 实现 REPLAN，先刷新状态再执行新计划。
- [x] 至少 5 个 Checkpoint 的三个动作均适用。
- [x] 每个 Checkpoint 完成三个候选动作，共 15 个分支。
- [x] 每个动作从独立副本恢复。
- [x] 同一 Checkpoint 三分支初始规范化状态哈希一致。
- [x] 文件系统不共享可变状态。
- [x] SQLite 数据库不共享可变状态。
- [x] Agent Memory / RuntimeState 不共享可变对象。
- [x] 动态串扰测试通过。
- [x] 所有结果由确定性验证器判定。
- [x] 分支成本和终止原因结构化记录。
- [x] smoke fixture 明确标记为非正式实验。
- [x] 未提前实施 Day 7。

## Known Issues

- 当前 Day 6 `REPLAN` adapter 针对 `api_ship_order/update_order` smoke 场景；跨四领域的通用计划生成与大规模模板覆盖属于后续任务，不在本日范围。
- 5 个 Checkpoint 来自同一个 API Workflow 任务实例，通过不同环境/运行态 marker 构成独立验收状态；它们验证 Branch 协议，不代表正式效用数据的任务多样性。
- 本 smoke 中 CONTINUE 与安全 RETRY 都执行同一个 pending `update_order`，因此最终环境结果相同；两者的动作语义、适用性路径和轨迹标签不同。实际效果差异需在 Day 9 多故障/多任务分支中测量。
- 本次没有远程模型请求，因此 Token 成本为 0；延迟仅代表本地工具和 SQLite 执行，不代表 Qwen/vLLM 延迟。
- 动态串扰探针是任务级文件、SQLite 和 JSON RuntimeState 验证，不是进程内存、Docker Volume 或 CRIU 级隔离。

## Remaining Work

- Day 6 核心验收无遗留阻塞项。
- 等待用户确认后才可进入 Day 7；Day 7 将扩展四领域任务模板和实例，本次未开始。

## Status: PASS

5 个 Checkpoint 的 15 个分支均从独立副本恢复，初始哈希一致、动态串扰检查通过、全部结果通过确定性验证，满足 Day 6 核心验收。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
