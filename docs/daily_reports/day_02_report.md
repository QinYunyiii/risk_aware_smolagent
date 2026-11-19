# Day 02 Report

## Objectives

- 在本地 WSL 使用项目级 Python 3.12 `.venv` 接入 `smolagents`。
- 实现两个远程 Qwen3-8B HTTPS Worker 的显式选择与轮询路由。
- 实现安全 CalculatorTool、本地工具执行证据、结构化轨迹和模型故障分类。
- 在共享服务器仅选择实时空闲 GPU，完成双 vLLM 的真实模型验收，并在验收后释放本次资源。
- 本日不实现 Day 3 的四领域任务环境。

## Local Environment Work

- 工作区：`/home/three/projects/risk-aware-smolagents`（WSL Ubuntu）。
- Python：CPython 3.12.3；项目环境：`.venv`。
- 未安装 Conda，未修改系统 Python。
- 顶层依赖已固定在 `requirements.txt` / `pyproject.toml`，完整解析版本记录在
  `requirements.lock`。
- `python -m pip check`：`No broken requirements found.`
- `.gitignore` 已排除 `.venv/`、密钥、模型、运行轨迹和 Checkpoint。

## Remote Environment Work

- SSH：`root@121.48.161.243:4035`，严格校验已知主机，使用独立审计密钥。
- 启动前检查全部 8 张 RTX 4090，而不复用上次的 GPU 编号假设。
- 2026-11-17 23:55 起（Asia/Shanghai）连续采样最初未发现空闲 GPU：
  GPU 0/2 有约 4.2 GiB 且高利用率，GPU 1 有约 22.0 GiB，GPU 4/5/6 各约
  20.9 GiB，GPU 3/7 有约 1.0--1.4 GiB 且高利用率。
- `nvidia-smi --query-compute-apps` 进一步确认 8 张卡均存在其他计算进程；未终止、覆盖或占用这些进程。
- 前 13 轮幂等检查正确拒绝启动；第 14 轮确认 GPU 0/2 连续两次为 15 MiB、
  0% 且无 compute process 后才启动。
- 两个独立实例分别使用 GPU 0 / GPU 2、端口 8000 / 8001；`tensor_parallel_size=1`，
  只监听 `127.0.0.1`，Hermes Tool Parser、TLS 和 API Key 均启用。
- 验收后只向状态文件和命令行双重验证的本次 PID 334838 / 334840 发送 TERM。
  GPU 0/2 均恢复为 15 MiB、0%，无 compute process；Worker 0/1 最终均为 `absent`。

## Work Completed

- 实现集中式远程模型配置、HTTPS 强制校验、API Key / CA 环境变量解析。
- 实现线程安全的显式 Worker 选择和 round-robin；有副作用工具执行后禁止自动切换 Worker。
- 实现基于 AST 白名单的 CalculatorTool，未使用不受限 `eval()`。
- 工具事件记录参数、结果、错误、延迟、主机、PID、工作目录和执行位置。
- 实现 TLS 校验的 OpenAI-compatible 客户端与 `ToolCallingAgent` 工厂。
- 将模型超时、TLS、不可用、鉴权、限流和响应错误与 Agent 任务失败分开分类。
- 实现追加式 JSONL 轨迹记录。
- 实现普通对话验收脚本（每 Worker 5 次）和 10 次真实工具调用验收脚本。
- 完成 Worker 0 的 13 次、Worker 1 的 6 次成功普通对话；两个 `/v1/models` 均返回
  `Qwen/Qwen3-8B`。
- 追加轨迹累计 11 次真实 Calculator 调用，11 次工具执行成功且全部位于本地 WSL；
  9 次 Agent 完整成功，满足工具调用成功不少于 9 次的核心标准。
- 实现远端双 Worker 与本地隧道的幂等 start/status/wait/stop 管理：
  - 启动前连续两次采样显存与利用率；
  - 排除任何存在 compute process 的 GPU；
  - 检查自有 PID、命令行、端口和健康状态；
  - SSH 超时后要求先检查状态，不将超时等同于启动失败；
  - 只停止状态文件和命令行双重验证属于本次 Day 2 的 PID；
  - vLLM 仅监听远端 loopback，SSH 隧道启用 `ExitOnForwardFailure`。

## Files Changed

- `pyproject.toml`
- `requirements.txt`
- `requirements.lock`
- `.env.example`
- `configs/remote_model.yaml`
- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/agent/worker_router.py`
- `src/risk_agent/tools/calculation_tools.py`
- `src/risk_agent/monitoring/trajectory.py`
- `scripts/manage_remote_vllm.py`
- `scripts/check_remote_api.py`
- `scripts/smoke_test.py`
- `tests/test_agent.py`
- `docs/daily_reports/day_02_report.md`

## Artifacts Generated

- 完整依赖锁定：`requirements.lock`。
- 普通对话与故障检查：`runs/day02/remote_api_checks.jsonl`。
- Agent / 工具调用轨迹：`runs/day02/agent_trajectories.jsonl`。
- 验收汇总：`runs/day02/smoke_summary.json`。
- 以上为真实 Qwen3-8B 运行产物，按 `.gitignore` 不提交 Git；失败尝试也按追加原则保留。

## Commands and Tests

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install \
  'smolagents[openai]==1.26.0' 'openai==3.52.0' 'PyYAML==6.0.3' \
  'pytest==9.2.0' 'pytest-timeout==2.4.0'
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q

ssh ... 'nvidia-smi --query-gpu=...; nvidia-smi --query-compute-apps=...'
.venv/bin/python scripts/manage_remote_vllm.py status
.venv/bin/python scripts/manage_remote_vllm.py start
```

真实 GPU Gate 满足后执行：

```bash
.venv/bin/python scripts/manage_remote_vllm.py wait-health
.venv/bin/python scripts/manage_remote_vllm.py tunnel-start
.venv/bin/python scripts/check_remote_api.py
.venv/bin/python scripts/check_remote_api.py --skip-chats
.venv/bin/python scripts/smoke_test.py
.venv/bin/python scripts/smoke_test.py --task-indices 1,2
.venv/bin/python scripts/manage_remote_vllm.py tunnel-stop
.venv/bin/python scripts/manage_remote_vllm.py stop
```

## Test Results

- `pip check`：PASS，无损坏依赖。
- `pytest`：PASS，15 passed。
- `ToolCallingAgent` / `OpenAIServerModel` 本地构造：PASS。
- CalculatorTool 安全语法、危险输入拒绝、Router、错误分类、JSONL 追加：PASS。
- 密钥字面量扫描：未发现私钥、Bearer Token 或 API Key 值进入仓库文件。
- TLS 禁用扫描：正式代码未出现 `verify=False`、`curl -k` 或 `--insecure`。
- 远端空闲 GPU Gate：PASS；只选择后来释放且连续验证空闲的 GPU 0/2。
- 双 Endpoint：PASS；Worker 0/1 模型列表和正确 CA TLS 预检均通过。
- 普通对话：PASS；Worker 0 = 13，Worker 1 = 6。
- Worker Router：PASS；真实轨迹同时包含成功的显式路由与 round-robin 路由。
- Tool Call：PASS；11 次调用、11 次工具执行成功、9 次 Agent 完整成功。
- 本地执行：PASS；11/11 工具事件记录 `execution_location=local_wsl`。
- 轨迹：PASS；完整成功记录均包含 Worker、工具参数、结果、Token 和延迟；模型服务
  中断发生在工具后时显式记录 `token_usage_complete=false`，不冒充任务失败。
- 故障处理：PASS；错误 CA = `model_api_tls_error`，关闭端口 =
  `model_api_unavailable`，受控挂起连接 = `model_api_timeout`。
- 资源释放：PASS；隧道关闭，GPU 0/2 恢复 15 MiB、0%，无残留计算进程。

## Acceptance Checklist

- [x] 项目级 Python 3.12 `.venv` 安装成功。
- [x] 依赖版本固定，`pip check` 通过。
- [x] Worker Router 显式选择与轮询通过离线测试。
- [x] 安全 CalculatorTool 和本地执行位置记录已实现。
- [x] Worker、参数、结果、Token、延迟的轨迹字段已实现。
- [x] TLS 强制开启，API Key 仅由环境变量读取。
- [x] 超时、TLS、服务不可用的错误类别有单元测试。
- [x] SSH 启动幂等检查和资源所有权保护已实现并验证安全拒绝。
- [x] Worker 0 至少 5 次真实对话。
- [x] Worker 1 至少 5 次真实对话。
- [x] 至少 10 次真实工具调用且成功不少于 9 次。
- [x] 确认全部真实工具调用在本地 WSL 执行。
- [x] 完成真实 TLS、超时和服务不可用集成检查。
- [x] 未占用或终止其他用户 GPU 进程。
- [x] 当前无本次启动的 vLLM 服务或 SSH 隧道需要释放。

## Known Issues

- 共享服务器 SSH 传输在验收期间多次握手超时或中断。启动逻辑始终先复查 PID、端口和
  健康状态，未重复创建服务；只读对话有上限重试，工具任务仅在零工具事件时允许重试。
  ControlMaster 隧道退化后，最终验收使用前台受控 SSH 隧道，验收结束后显式关闭并确认
  本地端口关闭。
- `openai==3.52.0` 使用的 `httpx2` 在错误 CA 场景曾把握手异常呈现为超时；因此增加了
  基于 Python `ssl` 的显式证书链/主机名预检。正式请求仍使用正确 CA 且未关闭 TLS 校验。

## Remaining Work

- Day 2 无核心遗留项。进入 Day 3 前等待用户确认。
- 后续长实验应继续使用可续跑、追加式轨迹，并将 SSH 传输中断与任务环境失败分开。

## Status: PASS

Day 2 核心验收全部通过；本次 vLLM 和 SSH 隧道均已停止，GPU 资源已恢复。

## Git Commit

未提交；用户未要求创建提交。当前仓库原有文件均为未跟踪状态，本次未覆盖用户提交历史。
