# Risk-Aware Tool-Using Agent

> **项目代号：MiniToolBench-R**\
> 基于 smolagents 和 Qwen3-8B 的工具调用 Agent
> 失败风险预测、干预效用估计与自适应干预研究。

**项目状态：** 14 日工程原型完成；Day 14 验收通过，研究结论仍需独立 Validation/Test\
**实施周期：** 14 天\
**Agent 框架：** smolagents\
**基础模型：** Qwen3-8B\
**推理服务：** 远程 vLLM HTTPS API\
**实验控制端：** 本地 Windows + WSL Ubuntu 24.04\
**模型推理端：** 共享实验室服务器；当前 SSH 容器可见 8 × RTX 4090 24GB，每次动态选择两张空闲卡\
**已有环境：** 本地 Recovery-Bench / Docker；Harbor 位置以审计结果为准

## 1. 项目简介

Tool-Using Agent
通过多步工具调用完成复杂任务。工具超时、参数错误、状态过期、重复操作和部分成功可能导致任务失败。固定重试规则难以判断何时需要干预，也难以判断当前状态下哪种干预更有效。

本项目构建 Risk-Aware Tool-Using
Agent：利用执行轨迹预测失败风险，估计 Continue、Retry、Replan
的条件成功概率和额外成本，并在安全约束下选择动作。

项目重点是建立可复现的同状态分支实验，比较不同动作在相同执行状态下的实际结果。

当前工程交付已包含四领域 40 个模板/200 个实例、F0–F6 故障、任务级
Checkpoint/Restore、隔离 Branch Runner、风险/效用模型、自适应策略、基线与消融，以及只读 Recovery-Bench/Harbor Adapter。当前学习和策略指标来自 20 个受控 Train Checkpoint，不能解释为独立 Test 泛化结果。

## 2. 系统架构：本地实验控制 + 远程模型推理

``` text
本地电脑（Windows + WSL Ubuntu 24.04）
├── Recovery-Bench
├── Docker 任务环境
├── MiniToolBench-R
├── smolagents
├── 本地工具执行
├── 轨迹采集 / 故障注入
├── Checkpoint / Restore / Branch Runner
├── 风险预测 / 效用估计 / 策略
└── 实验日志
          |
          | HTTPS API
          v
远程共享实验室服务器（当前 SSH 入口为 Ubuntu 22.04 Docker 容器）
├── 空闲 RTX 4090 A：Qwen3-8B / vLLM Worker 0 / Port 8000
└── 空闲 RTX 4090 B：Qwen3-8B / vLLM Worker 1 / Port 8001
```

### 本地职责

-   保留现有 Recovery-Bench；
-   运行 MiniToolBench-R 和 smolagents；
-   执行本地工具和 Docker 任务；
-   采集轨迹、故障注入、保存状态和运行分支；
-   训练和评估风险模型、效用模型与策略。

### 远程职责

-   保存 Qwen3-8B 权重；
-   从 8 张共享 GPU 中动态选择两张当时无人使用的卡；
-   在选定空闲卡上部署两个独立 vLLM 实例；
-   提供 TLS 保护的 HTTPS OpenAI-compatible API。

模型权重不需要下载到本地。模型返回 Tool Call 后，由本地 smolagents
执行工具；远程模型不会因此自动获得本地 Docker 或文件系统访问权限。两个
Worker 是独立模型实例，不是 Tensor Parallel。每次运行必须记录 Worker 与物理 GPU ID 的映射；不得抢占、终止或影响其他实验室用户的任务。

当前时间受限的 GPU 0/1 常驻部署状态和安全停止步骤见 [远程部署状态](docs/remote_deployment.md)。该记录不把临时租约改成永久资源分配。

## 3. 研究问题

-   **RQ1：** 当前及历史执行轨迹能否预测 Agent 后续任务失败风险？
-   **RQ2：** 相同执行状态下，Continue、Retry 和 Replan 的结果是否不同？
-   **RQ3：** 能否预测不同动作的条件成功概率和执行成本？
-   **RQ4：** 风险与效用联合决策能否改善可靠性与成本权衡？

## 4. 方法概览

系统模块： 1. Agent Execution Engine：本地 smolagents 调用远程
Qwen3-8B，并在本地执行工具。 2. Trajectory
Monitor：记录工具调用、参数、结果、异常、Token 和延迟。 3. Failure Risk
Predictor：预测当前执行状态的失败风险。 4. Intervention Utility
Estimator：估计 Continue、Retry、Replan 的条件成功概率和成本。 5.
Adaptive Intervention Policy：结合风险、效用和安全约束选择动作。 6. Task
Evaluator：通过确定性验证器判断任务是否完成。

  动作       含义
  ---------- --------------------------------
  CONTINUE   沿当前计划继续
  RETRY      满足安全条件时重试当前工具操作
  REPLAN     根据当前状态和错误信息重新规划
  STOP       触发安全约束或预算上限时终止

写操作、部分成功和状态不确定时不允许盲目 Retry。

## 5. MiniToolBench-R 任务环境

  领域                  任务示例                       环境
  --------------------- ------------------------------ -------------------
  Data Analysis         CSV 过滤、统计、聚合           Python / CSV
  File Retrieval        文件查找、文档信息提取         本地隔离目录
  API Workflow          订单、库存、状态查询与更新     Mock API / SQLite
  Calculation & Logic   多步计算、单位转换、条件判断   Python 工具

所有任务必须有确定性验证器，不能只用 LLM 自评判断成功。

## 6. 故障类型

计划覆盖 No Fault、Timeout、API 503、Invalid Argument、Stale
Observation、Repeated Action / No Progress、Partial Failure。Partial
Failure 必须区分操作是否提交和客户端是否收到成功响应。

## 7. 数据采集与分支实验

``` text
Task Execution
      |
      v
Checkpoint
      |
      +------ Restore ------ CONTINUE
      |
      +------ Restore ------ RETRY
      |
      +------ Restore ------ REPLAN
      |
      v
Compare Outcomes and Costs
```

每个分支记录任务结果、验证结果、额外
Token、额外工具调用、额外延迟、动作适用性、终止原因和状态哈希。同一
Checkpoint 的分支必须处于同一 Split，且不得共享可变状态。

## 8. 数据划分与防泄漏

按任务模板分组划分 Train / Validation / Test。同一模板实例和同一
Checkpoint 的全部分支不得跨
Split。风险预测特征只能使用决策时刻已知的信息。测试集只用于最终评估。

## 9. 评价指标

-   风险：AUROC、AUPRC、Brier Score、Precision、Recall、Calibration；
-   可靠性：Task Success Rate、Recovery Rate、Failure Rate、Validation
    Pass Rate；
-   干预：Intervention Rate、Unnecessary Intervention Rate、Retry /
    Replan / Stop Rate；
-   成本：Average Tokens、Average Latency、Average Tool Calls、Recovery
    Cost；
-   效用：Outcome Prediction Error、Cost Prediction Error、Utility
    Prediction Error、Policy Regret。

所有指标说明分母和口径，并按领域和故障类型分层报告。

## 10. 基线与消融

基线：Vanilla Agent、Always
Retry（仅安全适用）、Rule-based、Risk-based、Risk + Utility。

消融：Full、w/o Risk、w/o Utility、w/o Cost。

不预设方法在所有指标上都优于基线，应如实分析可靠性与成本权衡。

## 11. Recovery-Bench 复用策略

Recovery-Bench 已在本地，默认不迁移、不重建、不直接修改原仓库。Day 1
审计 Agent 执行入口、工具调用轨迹、Docker 资源、任务
reset、验证器、Checkpoint / Restore 和分支能力。

Harbor
的部署位置和接口以实际审计为准。普通失败日志不能自动替代同状态分支效用数据。若
Recovery-Bench 不支持完整状态恢复和分支执行，在新项目中实现 Adapter
和独立模块。

## 12. 项目目录

``` text
risk-aware-smolagents/
├── AGENTS.md
├── PROJECT_GUIDE.md
├── README.md
├── configs/
│   ├── local.yaml
│   ├── remote_model.yaml
│   └── experiment.yaml
├── src/risk_agent/
│   ├── agent/
│   ├── tools/
│   ├── monitoring/
│   ├── prediction/
│   ├── controller/
│   ├── recovery_bench/
│   ├── benchmark/
│   └── evaluation/
├── data/
├── models/       # 风险模型和效用模型，不存 Qwen 权重
├── scripts/
├── tests/
├── runs/
└── docs/
    ├── daily_reports/
    ├── reproducibility.md
    ├── final_report.md
    └── demo.md
```

详细开发要求见 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)。

## 13. 14 天计划

  Day   主要任务                                             状态
  ----- ---------------------------------------------------- ----------------
  1     本地/远程审计、仓库初始化                           CONDITIONAL PASS
  2     本地 smolagents 接入远程 Qwen3-8B HTTPS API         PASS
  3--7  任务、故障、状态、分支、200 实例                    PASS
  8     双 Worker 初始轨迹采集                              PASS
  9--13 干预数据、风险、效用、策略、基线与消融             PASS（工程验收）
  14    Recovery-Bench 只读扩展验证与交付                   PASS

## 14. 配置与安全原则

本地实验配置与远程模型配置分离。`configs/agent.yaml` 和各 Day 配置管理本地任务、模型元数据路径与实验设置；`configs/remote_model.yaml` 管理两个 Worker 的 Base URL、模型 ID、超时和 TLS CA 路径；`configs/day14_recovery_bench.yaml` 管理外部 Recovery-Bench 的只读路径与 checksum-pinned 样本。API Key 通过环境变量或私有运行配置读取，不写入 YAML 或 Git。

优先使用受控网络、VPN 或 SSH 隧道。TLS 校验必须开启，不得使用
`verify=False` 或 `curl -k` 作为正式方案。远程 API 故障必须与 Agent
任务失败分开记录。

## 15. 项目状态

14 日工程实现已经完成。最终本地测试、交付范围、实测结果和限制见：

- [复现指南](docs/reproducibility.md)
- [最终工程报告](docs/final_report.md)
- [Demo](docs/demo.md)
- [Day 14 日报](docs/daily_reports/day_14_report.md)

核心实测结果包括：Day 7 的 200/200 reference task、Day 8 的双 Worker 真实 Qwen3-8B 轨迹、Day 12 的 20/20 本地策略执行，以及 Day 13 的 9 策略 × 20 Checkpoint 配对实验。Day 10–13 使用同源 Train 数据；Full 与 Risk-based 和三个消融在当前数据上行为相同，尚不能支持正式泛化或组件增益结论。

快速验证：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_day14_recovery_bench_demo.py
```

公开仓库前检查第三方依赖和数据许可。不得提交模型权重、密钥、私钥、受限制日志或未经许可的数据。

## Phase 2 正式协议冻结 v2.1（2026-11-27）

正式候选配置为 [phase2_risk_collection_v2_1.yaml](configs/phase2_risk_collection_v2_1.yaml)：
单次 Completion 1024 tokens；新增多步骤 API 任务统一保留完整标识符；在首个合格的
非终止 Observation/Error 后捕获一个 primary Checkpoint。原配置及历史 Run 保留。
80 模板、160 实例及其 Split、Schema、故障分类、Verifier 和预算由独立 Manifest 校验。

本地检查：`.venv/bin/python scripts/verify_protocol_freeze.py`。
详细约束见 [冻结记录](docs/phase2/protocol_freeze_record_v2_1.md)。
本地冻结不代表正式采集已就绪：新配置仍须验收完整 CONTINUE 到终止及 Verifier，
并获得正式采集授权；历史 Smoke 数据不能作为正式训练数据。

当前实现修订为 [readonly-refresh-v1](docs/phase2/replay_guard_readonly_refresh_v1.md)：
允许新调用 ID 的只读查询，保留历史调用 ID 和不安全写入的重放保护。
默认冻结检查使用独立修订 Manifest；原 v2.1 配置、Manifest 和历史 Run 保留。

统一验收入口已接通首个合格点捕获、独立 Restore、完整 CONTINUE 和 Verifier：
`.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode local-preflight`。
使用 `--mode offline-smoke` 可运行五个 Train 任务的完整离线接线验证。
[入口说明](docs/phase2/continue_acceptance_entry_v1.md)包含真实验收的独立授权、
新鲜远端证据、资源释放和审计要求；离线结果不属于正式训练数据。
