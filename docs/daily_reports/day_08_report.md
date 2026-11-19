# Day 08 Report

## Objectives

- 本地 smolagents 调用两个远程 Qwen3-8B HTTPS Endpoint。
- 模型返回 Tool Call 后，在本地 WSL 隔离任务环境执行工具。
- 双 Worker 保存独立 task ID、Worker ID、模型、Token、工具、延迟与验证结果。
- 将模型/API 基础设施失败与 Agent/任务失败分离。
- 真实日志字段完整率达到至少 95%。
- 只完成 Day 8，不开始 Day 9 干预分支与效用数据。

## Local Environment Work

- 固定 Day 7 catalog hash：`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`。
- 采集 Train split 每个模板的 `_001` 实例，共 24 个任务、每领域 6 个。
- 实现 smolagents 本地工具 Adapter，每个 Agent 只获得当前领域的三个工具；工具实际由本地 `TaskToolRegistry` 和隔离 `TaskEnvironment` 执行。
- 记录 Agent succinct memory、输出、Token、工具历史、确定性验证、环境哈希、Worker、Endpoint 和 catalog hash。
- 实现失败来源：
  - `infra_failure / model_service`：模型超时、TLS、连接、鉴权、限流或 API 响应错误；
  - `task_failure / agent_or_tool`：模型请求完成但 Agent/工具未通过确定性验证；
  - `success / none`：任务完成且确定性验证通过。
- 实现追加式原始 JSONL 和按 task ID 续跑。
- 在正式采集中发现隧道中断后修正续跑协议：只有 `infra_failure` 且 `tool_call_count=0` 的任务可创建新 attempt；任何已执行工具的任务均不自动重跑。
- 新的 pre-tool 基础设施失败会立即暂停批次，避免连接中断造成级联无效记录。
- 原始 attempt 不删除、不覆盖；另生成 24 条唯一 task ID 的派生逻辑结果。

## Remote Environment Work

- 2026-11-19 用户明确授权使用 GPU 0 / 1。
- 启动前实时检查：GPU 0 / 1 均为 15 MiB、0%，Worker 0 / 1 absent；启动脚本再次连续采样并排除 compute process。
- 两个独立 vLLM 实例：
  - Worker 0：GPU 0，PID 377384，远端 loopback 端口 8000；
  - Worker 1：GPU 1，PID 377386，远端 loopback 端口 8001。
- 两实例均使用 Qwen3-8B、`tensor_parallel_size=1`、Hermes Tool Parser、TLS 和 API Key，只监听远端 `127.0.0.1`。
- 首次启动命令因 SSH 无输出返回；先用 `status` 核实 PID、端口和 GPU 后只执行 `wait-health`，没有重复启动。
- 健康状态时 GPU 0 / 1 各占约 21.6--21.8 GiB，两个 `/v1/models` 均通过。
- 通过受控 SSH ControlMaster 转发本地 8000 / 8001 到远端 loopback。
- 首个 ControlMaster PID 556150 在正式批次期间中断；远端 Worker 始终健康。重新核实状态后建立 PID 557137 的新隧道并使用相同 run ID 续跑。
- 验收结束后停止隧道，只向状态文件和命令行双重验证的 PID 377384 / 377386 发送 TERM。
- 最终释放检查：GPU 0 / 1 均恢复 15 MiB、0%，无 compute process；Worker 0 / 1 均 absent。
- 未终止、覆盖、reset 或影响其他用户的 GPU 进程。

## Work Completed

- 双 Worker 真实 TLS smoke：2/2 任务成功，两端各有真实请求和本地工具调用。
- 正式初始批次：24 个唯一 Train task，两 Worker 各 12 个。
- 四领域均产生真实本地工具调用。
- 共保存 43 条追加式原始 attempt，`task_run_id` 43/43 唯一。
- 首轮隧道中断产生的 pre-tool infra attempts 全部保留；恢复后只重跑安全适用的 19 个任务。
- 派生逻辑结果包含 24 个唯一 task ID：
  - `success`：19；
  - `infra_failure`：4；
  - `task_failure`：1。
- 22/24 任务的最终环境/工具结果通过确定性验证。
- 26 次真实本地工具调用：Worker 0 = 14，Worker 1 = 12；其中 3 次工具错误被如实记录。
- 20/24 个逻辑任务有完整模型 Token；4 个连接/超时基础设施失败保留空 Token，而不是填入合成值。
- 任务日志完整率 99.28%，工具日志完整率 95.24%。
- 正式 summary 的全部 Day 8 Gate 为 true，`passed=true`。
- 服务故障未记为任务失败；已执行工具后的模型连接失败未自动重试。

## Files Changed

- `configs/day8_collection.yaml`
- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/collection/__init__.py`
- `src/risk_agent/collection/benchmark_tools.py`
- `src/risk_agent/collection/initial_trajectory.py`
- `scripts/run_day8_collection.py`
- `tests/test_day8_collection.py`
- `docs/daily_reports/day_08_report.md`

未修改 Recovery-Bench、Day 7 split、研究问题、动作语义、风险标签、Checkpoint / Restore 协议或本地/远程职责边界。

## Artifacts Generated

Smoke run：`runs/day08/day8_smoke_20261119_073940_9c741ffd/`

- `preflight.json`
- `task_trajectories.jsonl`
- `tool_trajectories.jsonl`
- `collection_summary.json`

正式 run：`runs/day08/day8_20261119_074032_7ec4c41e/`

- `preflight.json`：双 Worker TLS、模型和 Endpoint preflight。
- `task_trajectories.jsonl`：43 条不可覆盖的原始 attempt。
- `tool_trajectories.jsonl`：26 条本地工具事件。
- `logical_task_results.json`：每个任务取最新安全 attempt 后的 24 条唯一逻辑结果。
- `collection_summary.json`：最终验收汇总，`passed=true`。

所有产物通过 run ID 和 catalog SHA-256 追溯；`runs/` 由 `.gitignore` 排除。

## Commands and Tests

```bash
# 远端状态、启动、健康等待与隧道
.venv/bin/python scripts/manage_remote_vllm.py status
.venv/bin/python scripts/manage_remote_vllm.py start
.venv/bin/python scripts/manage_remote_vllm.py wait-health
.venv/bin/python scripts/manage_remote_vllm.py tunnel-start

# 双 Worker smoke 与正式采集
.venv/bin/python scripts/run_day8_collection.py --smoke
.venv/bin/python scripts/run_day8_collection.py

# 隧道恢复后按原 run ID 安全续跑
.venv/bin/python scripts/run_day8_collection.py \
  --run-id day8_20261119_074032_7ec4c41e

# 资源释放
.venv/bin/python scripts/manage_remote_vllm.py tunnel-stop
.venv/bin/python scripts/manage_remote_vllm.py stop

# 本地验证
.venv/bin/python -m pytest -q tests/test_day8_collection.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

API Key 由用户私有配置文件读入环境变量；命令输出、仓库和轨迹均不包含密钥内容。

## Test Results

- Day 8 专项测试：3 passed。
- 全量回归：50 passed。
- 依赖：`No broken requirements found.`
- Python 编译：通过。
- 双 Worker smoke：2/2 success，Worker 0 / 1 各一次请求与一次本地工具调用。
- 双 Endpoint：TLS 校验和 `/v1/models` 均通过。
- 正式逻辑任务：24，task ID 24/24 唯一。
- 原始 attempts：43，task run ID 43/43 唯一。
- Worker 请求：12 / 12。
- Worker 工具调用：14 / 12。
- 四领域工具覆盖：通过。
- 最终状态：19 success / 4 infra failure / 1 task failure。
- 确定性验证：22/24 通过。
- Token 完整：20/24；缺失项均为基础设施失败。
- 任务日志完整率：99.28%。
- 工具日志完整率：95.24%。
- 本地执行：26/26 工具记录为 `local_wsl`。
- 敏感信息扫描：未发现 Authorization、private key 或 Bearer 内容。
- 资源释放：GPU 0 / 1 均为 15 MiB、0%，无 compute process；Worker absent。

## Acceptance Checklist

- [x] 固定并核验 Day 7 catalog hash。
- [x] 24 个唯一 Train task，四领域各 6 个。
- [x] 两张 RTX 4090 启动前确认为空闲。
- [x] 两个独立 vLLM Worker 健康运行。
- [x] 两个 Endpoint 通过真实 TLS 和模型 preflight。
- [x] Worker 0 有真实任务请求。
- [x] Worker 1 有真实任务请求。
- [x] 两 Worker 均有真实本地工具调用。
- [x] 四个任务领域均有真实本地工具调用。
- [x] task ID 唯一，attempt 使用独立 task run ID。
- [x] 模型服务失败与 Agent/任务失败分开记录。
- [x] 工具调用发生后不自动重试或切换 Worker。
- [x] 轨迹记录 Worker、工具参数、结果、Token、延迟和验证结果。
- [x] 任务和工具日志完整率均达到至少 95%。
- [x] TLS 校验开启，密钥未进入轨迹。
- [x] 原始轨迹只追加，失败 attempt 未删除或覆盖。
- [x] 中断后按同一 run ID 安全续跑。
- [x] 本次隧道和 vLLM 服务已停止。
- [x] GPU 0 / 1 占用已恢复。
- [x] 未提前执行 Day 9。

## Known Issues

- SSH ControlMaster 在首轮正式采集中断，造成一批工具执行前的 infra attempts。续跑协议已修复为遇到首个 pre-tool infra failure 即暂停，并只允许安全的 pre-tool attempt 重试。
- 4 个最终逻辑任务为 infra failure。其中 3 个已经得到正确工具结果并通过确定性验证，但模型在最终回答阶段发生连接错误；它们仍保留为 infra failure，不美化为 success。
- `api_order_processing_read_001` 首次使用错误 order ID，工具安全拒绝后模型请求超时；因为已有工具调用，没有自动重跑，最终验证失败。
- `logic_convert_metres_kilometres_001` 两次使用不受支持的全称单位，而工具要求 `m` / `km`，最终为真实 task failure。
- 任务日志完整率不是 100%，因为基础设施错误没有可用 Token；没有使用合成 Token 填补。
- 本批次是每个 Train 模板一个实例的初始轨迹，不是全部 200 实例的完整主实验。

## Remaining Work

- Day 8 核心验收已通过，无阻塞项。
- Day 9 前应只从有效 Checkpoint 选择分支；infra failure 不能作为 Agent 失败标签。
- Day 9 数据生成必须继续保持同一 Checkpoint 的全部分支位于同一 split，并记录动作适用性与成本。
- 等待用户确认后才进入 Day 9。

## Status: PASS

双 RTX 4090、双 HTTPS Endpoint、双 Worker 真实请求、四领域本地工具轨迹、失败来源分离和 ≥95% 日志完整率均通过；本次远程服务和 GPU 资源已释放。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
