# Day 1 Architecture Baseline

本文冻结 Day 1 审计后的职责边界与后续依赖。它不代表 Day 2–14 功能已经实现。

## 1. 部署架构

```text
本地 Windows + WSL Ubuntu 24.04                         远程 Ubuntu 24.04
┌──────────────────────────────────────────────┐       ┌───────────────────────────┐
│ MiniToolBench-R / smolagents                 │ HTTPS │ 空闲 GPU A: Qwen + vLLM │
│ Worker Router + model client                 ├──────►│ Worker 0 / :8000 /v1     │
│ Recovery-Bench Adapter                       │       ├───────────────────────────┤
│ Local tools + Docker task environments       │ HTTPS │ 空闲 GPU B: Qwen + vLLM │
│ Trajectory / fault injection / verifier      ├──────►│ Worker 1 / :8001 /v1     │
│ Checkpoint / Restore / Branch Runner         │       └───────────────────────────┘
│ Risk / Utility / Policy / experiment logs    │
└──────────────────────────────────────────────┘
```

远程端只做模型推理。远程 vLLM 返回文本或 Tool Call 后，本地 smolagents 决定并执行本地工具；远程模型不会直接访问 WSL 文件、Docker socket 或 task database。远程有 8 张共享 RTX 4090，每次运行只动态选择两张当时空闲的卡，并记录 Worker 到物理 GPU ID 的映射。

## 2. 职责边界

| 能力 | 本地 WSL | 远程服务器 |
|---|---:|---:|
| Recovery-Bench / Harbor Docker trial | 是 | 否 |
| MiniToolBench-R 与 smolagents | 是 | 否 |
| 工具调用与副作用 | 是 | 否 |
| 任务 reset / verifier | 是 | 否 |
| 轨迹、故障、Checkpoint/Restore/Branch | 是 | 否 |
| 风险/效用模型训练与策略评估 | 是 | 否 |
| Qwen3-8B 权重 | 否 | 是 |
| vLLM inference | 否 | 是 |
| HTTPS OpenAI-compatible API | client | server |

禁止默认迁移 Recovery-Bench、远程重建本地 Docker、在本地下载 Qwen 权重，或让远程模型直接执行本地工具。

## 3. HTTPS API 与工具执行流程

```text
1. Local Task Scheduler 创建 experiment_id/run_id/task_id
2. Local Agent 选择 worker_id，并记录 model/prompt/inference config
3. Model Client 使用 HTTPS + CA 验证 + API key 环境变量请求远程 /v1
4. 远程 Worker 返回普通文本或结构化 Tool Call
5. Local Agent 校验工具名、参数、安全约束、预算和幂等条件
6. Local Tool Executor 在 WSL / Docker task environment 执行
7. Trajectory Monitor 记录参数、结果、错误、token、latency、worker/environment ID
8. 本地把工具 observation 发送给同一模型会话，或按明确策略处理 API failure
9. Deterministic Verifier 判定任务结果
```

安全要求：

- TLS 证书链与主机名必须验证；不使用 `curl -k` 或 `verify=False`。
- API key 只从环境变量或安全密钥管理读取，不写入配置和日志。
- 模型 API 超时/不可用属于 infra/API failure，不能自动记为 task/agent failure。
- Worker 切换只能重试尚未产生本地工具副作用的模型请求；不得重复执行状态不明的写操作。
- 每次请求记录 endpoint（可脱敏）、worker_id、model/version、prompt/version、推理参数、latency 和 error category。

## 4. 本地 Agent 与状态管理边界

```text
Agent state
├── conversation / memory
├── tool call history
├── budget / retry counters
└── model + prompt + seed config

Task environment state
├── task files
├── SQLite database
├── Mock API / service state
├── tool-specific state
└── Docker environment identity

Checkpoint manifest
├── immutable provenance
├── copied state components
├── component checksums
└── normalized initial_state_hash
```

只有上述必要状态都被保存并能恢复到相同规范化 hash 时，才可从一个 checkpoint 建立 Continue/Retry/Replan 独立分支。`docker commit` 或 shell command replay 单独使用都不满足该定义。

## 5. Recovery-Bench 与新项目的关系

```text
Recovery-Bench / Harbor（保留原位，只读）
    │
    ├── task metadata / Docker task definitions
    ├── Harbor verifier
    ├── result.json / ATIF trajectory / usage / logs
    └── historical failure traces
              │
              ▼
    RecoveryBenchAdapter（新项目）
    ├── schema validation + provenance
    ├── task/environment mapping
    ├── trajectory normalization
    └── verifier wrapper
              │
              ▼
    MiniToolBench-R core
    ├── local agent/tools/monitoring
    ├── checkpoint/restore/branch
    ├── fault injection
    └── risk/utility/policy evaluation
```

直接复用边界：任务启动思想、Harbor local CLI、Docker trial、verifier、ATIF/结果解析。  
新项目责任：Worker Router、统一轨迹 schema、故障分类、完整 Checkpoint/Restore、Branch Runner、state hash、安全动作语义、风险/效用/策略。

Recovery-Bench 的 recovery command replay 可作为诊断或一种候选恢复策略，但不能冒充 Checkpoint Restore，也不能直接生成同状态干预效用数据。

## 6. Day 2–14 模块依赖

| Day | 模块/结果 | 关键前置 | 对后续提供 |
|---:|---|---|---|
| 1 | 环境、复用与架构审计 | 指南与现有环境 | 本文、Adapter 边界、远程 Gate |
| 2 | Remote model client、Worker Router、本地 tool-call smoke | SSH/HTTPS/CA/双 endpoint 可验证 | 可追踪模型调用和本地工具执行 |
| 3 | 四领域 tasks、reset、deterministic verifier | Day 2 tool execution | 可复现 task environment |
| 4 | F0–F6 fault injection、安全检查 | Day 3 tasks/tools | 可控失败与明确副作用状态 |
| 5 | Trajectory Monitor、Checkpoint/Restore | Day 3–4 state schema | 可恢复的规范化状态与 hash |
| 6 | Continue/Retry/Replan Branch Runner | Day 5 Gate 必须通过 | 同状态独立分支结果 |
| 7 | 模板、实例、group split | Day 3 verifier + Day 6 protocol | 防泄漏任务集 |
| 8 | 初始真实轨迹采集 | Day 2 endpoint + Day 4 monitor/faults | Risk 数据基础 |
| 9 | 分支干预数据 | Day 6 + Day 7 split | Utility/Cost 数据基础 |
| 10 | Risk Predictor | Day 8 数据质量 Gate | 校准失败风险 |
| 11 | Utility/Cost Estimator | Day 9 数据质量 Gate | 候选动作 outcome/cost |
| 12 | Adaptive Policy | Day 10–11 models + safety | 风险/效用联合决策 |
| 13 | baselines、ablation、main experiment | Day 12 end-to-end | 分层主结果 |
| 14 | Recovery-Bench Adapter 扩展与交付 | 前述模块稳定 | 最终复现、报告、Demo |

关键 Gate：

```text
Day 1 remote connection Gate
  -> Day 2 real endpoint integration
  -> Day 3/4 deterministic local task and faults
  -> Day 5 Checkpoint/Restore hash Gate
  -> Day 6 comparable branches
  -> Day 8/9 valid datasets
  -> Day 10–13 modeling and evaluation
```

远程 SSH/GPU 已实测，实际目标是 Ubuntu 22.04 Docker 容器且可见 8 × RTX 4090 共享 GPU。用户确认可动态选择两张空闲卡，但不得抢占其他用户；部署前必须重新审计即时占用。隔离 vLLM 环境、Qwen3-8B、GPU 3/7 双实例和本地经 SSH 隧道的严格 HTTPS 已通过 smoke test；服务在验收后已停止并释放 GPU。Day 2 可复用该连接方案，但必须重新选择空闲卡、启动服务并验证即时健康状态。在 Checkpoint/Restore 未通过前，不得开始正式分支数据采集。

## 7. Day 1 已知基础与未决事项

已确认：

- Codex 位于本地 WSL Ubuntu 24.04；
- Recovery-Bench、Harbor 0.4.0 和本地 Docker engine 存在；
- Harbor task execution、ATIF、verifier 和结果格式可供 Adapter 复用；
- Recovery-Bench 不提供研究所需的完整 Checkpoint/Restore/Branch protocol；
- SSH Host Key 已通过服务器控制台确认，专用 key 严格校验登录成功；
- 实际远程入口是 Ubuntu 22.04 Docker 容器，暴露 8 张 RTX 4090；
- 8 张卡属于公共实验室资源；每次动态选择两张空闲卡的规则已由用户确认；
- Driver/CUDA toolkit、Conda/Python、磁盘和内存已审计；
- 远端 loopback 8000/8001 + 本地 SSH tunnel 的 HTTPS/CA/API Key 方案已验证；验收后服务已停止。

仍需 Day 2 处理：

- 每次启动时重新选择两张空闲 GPU，并记录 Worker/物理 GPU 映射；
- 保留已验证的 FlashAttention v2 + PyTorch sampler fallback，或在改变 kernel 后重新验收；
- 为 SSH 隧道增加幂等启动、健康检查和受控重连，防止入口抖动导致重复模型进程；
- 正式客户端配置、smolagents Tool Call 和本地工具执行属于 Day 2，不在 Day 1 提前实现。

因此 Day 1 remote connection Gate 已有真实端到端证据，整体状态为 CONDITIONAL PASS。
