# Day 05 Report

## Objectives

- 记录工具调用、参数、结果、错误、Token、延迟及运行上下文。
- 保存 Agent Memory、工具调用历史、任务文件、SQLite、Mock API 状态、工具状态、预算、
  任务配置和 seed。
- 使用 SQLite Backup API 创建和恢复数据库快照。
- 验证恢复后的规范化组合状态哈希与快照完全一致。
- 不提前实现 Day 6 的 Branch Runner 或 Continue / Retry / Replan 分支。

## Local Environment Work

- 使用本地 Python 3.12 `.venv`、Day 3 任务环境和 Day 4 工具状态表示。
- Checkpoint 存储在唯一 Day 5 run 目录，已有 checkpoint ID 拒绝覆盖。
- 快照先写入权限隔离的 staging 目录，全部完成后原子发布。
- 恢复先校验全部 payload，再以可回滚方式替换任务工作区。

## Remote Environment Work

- Day 5 不需要远程模型、SSH、GPU 或 vLLM。
- 未启动远程服务，未占用共享 GPU，未修改 Recovery-Bench 或 Docker。

## Work Completed

- 实现 `ExecutionMonitor`：
  - 记录 experiment/run/task/template/instance/seed；
  - 记录模型、Prompt、工具版本、Worker 和 environment ID；
  - 记录 step、工具名、脱敏参数、脱敏结果、错误类别、Token、延迟和 checkpoint ID；
  - 成功和异常工具调用都追加到 JSONL 与 RuntimeState；
  - 在工具执行前拒绝负 Token 计数；
  - API Key、密码、Authorization、私钥、secret 和 access token 字段自动脱敏。
- 实现 checkpointable `RuntimeState`：Agent Memory、Tool History、Tool State、seed 和 Budget。
- 实现预算计数器：Token、工具调用和累计延迟及上限状态。
- 实现任务级 `CheckpointManager`：
  - 任务文件逐文件复制并拒绝 symlink；
  - SQLite snapshot / restore 均使用 Backup API；
  - Mock API 状态额外导出为规范化 JSON，并与 SQLite backup 交叉核对；
  - 保存 RuntimeState、完整 TaskSpec 和 manifest；
  - manifest 保存每个 artifact checksum 和 environment/runtime/task/mock/combined hashes；
  - manifest、checksum、任务 ID、任务配置和 seed 全部通过后才允许恢复；
  - 恢复使用 staging 和旧工作区回滚保护；
  - 恢复完成后再次计算规范化组合哈希，不一致则回滚。
- 组合状态哈希覆盖：文件内容哈希、规范化 SQLite 行、Agent Memory、工具历史、工具状态、
  预算、seed 和任务配置。
- JSONL writer 改为完整写循环并 `fsync`，避免短写导致截断记录。

## Files Changed

- `src/risk_agent/monitoring/runtime_state.py`
- `src/risk_agent/monitoring/execution_monitor.py`
- `src/risk_agent/monitoring/trajectory.py`
- `src/risk_agent/monitoring/__init__.py`
- `src/risk_agent/checkpoint/__init__.py`
- `src/risk_agent/checkpoint/schema.py`
- `src/risk_agent/checkpoint/manager.py`
- `src/risk_agent/benchmark/environment.py`
- `scripts/run_day5_checkpoint.py`
- `tests/test_checkpoint.py`
- `docs/daily_reports/day_05_report.md`

## Artifacts Generated

- 验收 run：`day5_20261118_024005_c9b581ec`（UTC 时间戳）。
- 汇总：`runs/day05/day5_20261118_024005_c9b581ec/checkpoint_results.json`。
- 轨迹：`runs/day05/day5_20261118_024005_c9b581ec/trajectory.jsonl`。
- Checkpoint：`runs/day05/day5_20261118_024005_c9b581ec/checkpoints/checkpoint_001/`。
- Checkpoint payload：
  - `manifest.json`；
  - `runtime_state.json`；
  - `task_config.json`；
  - `mock_api_state.json`；
  - `files/notes/progress.txt`；
  - `database/api.sqlite3`。
- 失败的早期 smoke run 目录保留，未删除或覆盖。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_day5_checkpoint.py
.venv/bin/python -m pip check
.venv/bin/python -m py_compile $(find src scripts tests -name '*.py' -type f)
```

## Test Results

- Unit / integration：42 passed。
- Trajectory：成功/异常工具调用均记录；Token、延迟、错误和 checkpoint ID 字段完整。
- Secret redaction：测试密钥和密码未进入 JSONL。
- Runtime state：Agent Memory、2 条工具历史、Tool State 和 Budget 完整恢复。
- Files：快照后修改的文件恢复；快照后新增文件被移除。
- SQLite：backup `PRAGMA integrity_check=ok`；订单恢复为 `shipped` / version 2。
- Mock API：动态库存从快照后 quantity 0 恢复为 quantity 12。
- State hash：
  - snapshot：`d1c11c8ab0329e0f48257cc7947f7fe846a17f8d0df93e83366ceb9c2b8b69b7`；
  - mutated：`132f32023e370817e76efc534dd068ba15fd5a82f33ec60afa53a37a4157437c`；
  - restored：与 snapshot 完全相同。
- Corruption：篡改 payload 在工作区变更前被拒绝。
- Symlink：checkpoint payload symlink 被拒绝。
- Overwrite：重复 checkpoint ID 被拒绝。
- Identity：错误 task 或错误 seed 被拒绝。

## Acceptance Checklist

- [x] 记录工具调用名称和参数。
- [x] 记录工具结果和错误类型。
- [x] 记录 Token 和延迟。
- [x] 保存 Agent Memory。
- [x] 保存工具调用历史。
- [x] 保存任务文件。
- [x] 使用 SQLite Backup API 保存数据库。
- [x] 保存并交叉验证 Mock API 状态。
- [x] 保存工具状态。
- [x] 保存预算计数器。
- [x] 保存任务配置和 seed。
- [x] Restore 后规范化组合状态哈希一致。
- [x] SQLite backup / restore 完整性通过。
- [x] 损坏 checkpoint 不改变当前环境。
- [x] 未实现或运行 Day 6 分支采集。

## Known Issues

- 当前 Agent Memory 使用可移植 JSON 表示，不是运行中 Python 对象或进程内存转储。
- 当前为任务级文件/SQLite/Mock API/RuntimeState 快照，不包含 Docker 内存、进程、网络连接或 CRIU。
- Day 5 smoke runner 使用本地 reference tools；Token 数是测试输入，不冒充远程模型真实消耗。
- SHA-256 与 manifest 用于本地完整性检测，不是带密钥的来源认证机制。
- Day 6 分支隔离、三个候选动作及跨分支串扰检查尚未实现。

## Remaining Work

- Day 5 核心 Gate 已通过，无核心遗留项。
- 等待用户确认后进入 Day 6；在此之前不开始正式分支采集。

## Status: PASS

轨迹完整性、任务级快照、SQLite Backup API、Restore 和规范化状态哈希 Gate 全部通过。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留。
