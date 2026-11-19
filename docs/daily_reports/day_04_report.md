# Day 04 Report

## Objectives

- 实现 F0 No Fault、F1 Timeout、F2 API 503、F3 Invalid Argument、
  F4 Stale Observation、F5 Repeated Action / No Progress、F6 Partial Failure。
- 使用固定 seed 和配置化触发条件，验证同 seed 可复现。
- 追加记录故障触发、阶段、提交状态、响应状态和错误类别。
- 实现动作安全检查，阻止写操作、部分成功或状态不确定时的盲目 Retry。
- 不提前实现 Day 5 Checkpoint / Restore。

## Local Environment Work

- 使用项目级 Python 3.12 `.venv` 和 Day 3 本地任务环境。
- 故障注入包裹本地 Tool Registry，不修改 Recovery-Bench。
- 所有场景使用唯一 `experiment_id` / `run_id`，事件使用 append-only JSONL。
- 事件参数只保存规范化 SHA-256，不将原始敏感参数写入故障日志。

## Remote Environment Work

- Day 4 不需要远程模型、SSH、GPU 或 vLLM。
- 未启动远程服务，未占用共享 GPU，未修改 TLS、模型或远端日志。

## Work Completed

- 新增严格 `configs/faults.yaml`，为 F0--F6 分别固定 profile、seed、目标工具和触发条件。
- 实现两种可复现触发方式：明确 call index 和 seed 驱动概率序列；两者不可同时配置。
- 实现结构化 `FaultEvent`：fault ID/type、seed、tool、call index、triggered、phase、
  commit status、response received、error type、argument hash 和 timestamp。
- 实现故障行为：
  - F0：工具正常执行并记录未触发事件；
  - F1：注入 timeout，保留 commit status；
  - F2：在工具执行前返回模拟 503；
  - F3：在参数验证阶段拒绝无效参数；
  - F4：返回配置的旧观察值；
  - F5：第一次调用正常，第二次相同调用不再执行底层工具并返回旧结果；
  - F6：支持 committed 和 not_committed；committed 场景在真实 SQLite 写入后丢失响应。
- 实现集中式工具副作用描述：read-only / write、是否存在 idempotency key。
- 实现 `RetrySafetyEvaluator` 和 `RetryGuard`：
  - read-only timeout 和明确执行前 503 可 Retry；
  - unchanged invalid arguments、stale observation、repeated action 禁止 Retry；
  - committed 或 unknown partial failure 禁止 Retry 并要求先检查状态；
  - 明确 not committed 的 partial failure 才允许 Retry。
- 实现 Day 4 本地验收 runner 和唯一结果目录，历史结果不覆盖。

## Files Changed

- `configs/faults.yaml`
- `src/risk_agent/faults/__init__.py`
- `src/risk_agent/faults/schema.py`
- `src/risk_agent/faults/config.py`
- `src/risk_agent/faults/injector.py`
- `src/risk_agent/faults/operations.py`
- `src/risk_agent/faults/safety.py`
- `scripts/run_day4_faults.py`
- `tests/test_faults.py`
- `docs/daily_reports/day_04_report.md`

## Artifacts Generated

- 验收 run：`day4_20261118_011214_085d7497`（UTC 时间戳）。
- 汇总：`runs/day04/day4_20261118_011214_085d7497/fault_results.json`。
- 追加事件：`runs/day04/day4_20261118_011214_085d7497/fault_events.jsonl`。
- 汇总包含每个故障的安全决策、Retry Guard 结果、复现检查和 F6 实际数据库状态。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_day4_faults.py
.venv/bin/python -m pip check
.venv/bin/python -m py_compile $(find src scripts tests -name '*.py' -type f)
```

## Test Results

- Unit / integration：35 passed。
- F0--F6：7/7 场景通过，0 failed。
- Reproducibility：7/7 场景相同 seed 的触发签名一致。
- Seeded probability：24 次调用序列在两个独立 injector 中完全一致，且同时包含触发/不触发。
- F5：触发事件为 calculator call index 2；第二次未执行底层工具，Retry 被阻止。
- F6 committed：订单实际为 `shipped` / version 2，响应未收到，Retry 被阻止且要求状态查询。
- F6 not committed：底层写函数未执行，Retry 可安全允许。
- F3/F4/F5/F6 committed：不安全 Retry 均由 `RetryGuard` 阻止。
- F1 read-only / F2 before-execution：Retry 被明确允许。
- Fault log：JSONL 追加有效，包含 seed、commit status 和 argument hash。

## Acceptance Checklist

- [x] 实现 F0 No Fault。
- [x] 实现 F1 Timeout。
- [x] 实现 F2 API 503。
- [x] 实现 F3 Invalid Argument。
- [x] 实现 F4 Stale Observation。
- [x] 实现 F5 Repeated Action / No Progress。
- [x] 实现 F6 Partial Failure。
- [x] 故障按 YAML 配置触发。
- [x] 固定 seed 且同 seed 可复现。
- [x] 故障触发日志结构化、追加保存。
- [x] Partial Failure 可区分 committed / not_committed / unknown。
- [x] 不安全 Retry 可阻止。
- [x] 写操作状态不确定时要求先查询状态。
- [x] 未实现 Day 5 Checkpoint / Restore。

## Known Issues

- 当前故障均为本地、受控的 Day 4 smoke injection，不代表真实网络或第三方 API 的统计分布。
- Timeout 为快速确定性注入，不通过实际长时间 sleep 制造；阶段和提交状态由配置显式给出。
- Retry Guard 是安全规则层，不是 Day 12 的自适应策略或学习模型。
- 当前事件日志不包含 Day 5 的 Agent Memory、Token、Checkpoint ID 或恢复状态哈希。

## Remaining Work

- Day 4 无核心遗留项。
- 等待用户确认后进入 Day 5；Checkpoint / Restore Gate 通过前不得开始正式分支采集。

## Status: PASS

F0--F6、seed 复现、故障日志、Partial Failure 状态区分和 Retry 安全检查均通过。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留。
