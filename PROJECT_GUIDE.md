# PROJECT_GUIDE.md --- Risk-Aware smolagents / MiniToolBench-R

> 本文件是项目长期开发指南，供 Codex 每次新会话查阅。项目周期为 14
> 天，按验收结果推进。

## 0. 固定部署架构

本项目采用**本地实验控制、远程模型推理**，不得混淆两台机器的职责。

### 本地电脑：Windows + WSL Ubuntu 24.04

-   保留现有 Recovery-Bench 和 Docker 环境，不迁移、不重建。
-   运行 MiniToolBench-R、smolagents、任务调度和本地工具。
-   执行故障注入、轨迹采集、Checkpoint / Restore、分支实验。
-   训练风险预测模型、效用模型并运行策略评估。
-   保存实验数据、日志和报告。

### 远程服务器：共享实验室服务器，8 × RTX 4090 24GB

-   当前 SSH 入口实际为 Ubuntu 22.04 Docker 容器；宿主机操作系统未经审计。
-   容器可见 8 张共享 RTX 4090，不能抢占正在使用的卡。
-   Qwen3-8B 权重位置以实际审计为准。
-   每次从当时空闲且实验室规则允许使用的 GPU 中选择两张，运行两个独立 vLLM 实例。
-   提供 HTTPS OpenAI-compatible API。
-   不默认运行本地 Recovery-Bench 或本地 Docker 任务。

  Worker     GPU                          目标 Endpoint
  ---------- ---------------------------- ----------------------------
  Worker 0   动态选择的空闲 RTX 4090 A   `https://<server>:8000/v1`
  Worker 1   动态选择的空闲 RTX 4090 B   `https://<server>:8001/v1`

两个 Worker 是独立模型实例，不是 Tensor Parallel。权重可在远程磁盘共用一份，但每个实例分别占用对应 GPU 显存。物理 GPU ID 必须在每次启动前重新选择并记录，不能沿用上次映射。

空闲检查必须同时参考显存占用、GPU 利用率和实验室资源规则。当前 SSH 容器的 PID namespace 可能看不到其他容器进程，因此 `nvidia-smi` process 表为空不是充分条件。不得杀进程、reset GPU 或以其他方式抢占他人资源。

### 请求流程

``` text
本地任务 -> 本地 smolagents -> HTTPS 请求远程 vLLM
                                  |
                         返回文本或 Tool Call
                                  |
本地 smolagents <- 本地执行工具 <-+
       |
本地轨迹监控、验证、Checkpoint 和实验记录
```

模型推理发生在远程；工具执行默认发生在本地。模型不会因为返回 Tool Call
就自动访问本地 Docker 或文件系统。

### 网络与安全

-   优先使用受控内网、VPN 或 SSH 隧道；实际方式以网络条件为准。
-   不得将无鉴权 vLLM 端口直接暴露公网。
-   TLS 校验必须开启；不得用 `verify=False` 或 `curl -k` 作为正式方案。
-   API Key、SSH 私钥、TLS 私钥不得写入代码、Git 或普通日志。
-   Codex 只有在 SSH
    配置已存在或用户提供连接信息时才可执行远程命令。不得猜测主机名、用户名、端口、路径或密码。
-   SSH 不可用时，生成待用户执行的检查命令并请求结果。

## 1. 每次 Codex 新会话流程

1.  阅读 `AGENTS.md`、本文件和 `README.md`。
2.  检查 `git status --short`，保护未提交修改。
3.  阅读 `docs/daily_reports/` 最近一份日报。
4.  确认当前 Day、前置 Gate 和遗留问题。
5.  判断当天需要访问本地 Docker、远程 SSH 还是 HTTPS API。
6.  向用户汇报进度、当天目标、准备修改的文件和阻塞项。

一次只推进一个
Day。用户未同意时，不得自动进入下一天。没有运行的测试必须标记"未运行"，不得写成通过。

状态定义： - `PASS`：核心验收全部通过。 -
`CONDITIONAL PASS`：非核心事项未完成但不阻塞。 -
`BLOCKED`：核心功能或前置条件未通过。

## 2. 项目目标与研究问题

构建 Risk-Aware Tool-Using
Agent，监控工具调用轨迹，预测当前状态的失败风险，估计候选干预动作的条件成功概率与成本，并在安全约束下选择动作。

  -----------------------------------------------------------------------------------------------
  RQ                      研究问题                                        模块
  ----------------------- ----------------------------------------------- -----------------------
  RQ1                     当前及历史轨迹能否预测后续任务失败？            Failure Risk Predictor

  RQ2                     相同执行状态下不同干预动作的结果是否不同？      Checkpoint / Branch
                                                                          Runner

  RQ3                     能否预测不同动作的条件成功概率和执行成本？      Utility Estimator

  RQ4                     联合风险和效用决策能否改善可靠性---成本权衡？   Adaptive Policy
  -----------------------------------------------------------------------------------------------

动作： - `CONTINUE`：沿当前计划继续。 -
`RETRY`：满足安全条件时重试当前工具操作。 -
`REPLAN`：基于当前状态和错误信息重新规划。 -
`STOP`：触发安全条件、预算上限或不可恢复状态时终止。

写操作、部分成功或状态不确定时不得盲目
Retry，必须先查询状态或使用幂等键。

## 3. Recovery-Bench / Harbor 复用原则

Recovery-Bench 已在本地，默认保留原位置，不直接修改原仓库。Day 1
必须审计： - Agent 执行入口、`pipeline.py`、`per_task.json`
和任务生命周期； -
工具调用参数、返回、错误、Token、延迟是否有逐步日志； - 本地 Docker
Images、Containers、Volumes、Mounts； - 任务 reset 和确定性验证器； -
Checkpoint / Restore 是否真实支持； - 能否从同一状态创建隔离分支。

Harbor 的部署位置、接口和访问方式必须实际核实，不得假设与 Recovery-Bench
在同一台机器。

普通失败轨迹可用于失败模式分析，但不自动构成干预效用数据。效用实验必须从同一逻辑状态恢复独立分支并执行候选动作。

  模块                   原则
  ---------------------- ----------------------------
  Recovery-Bench 代码    审计后复用，不直接改原仓库
  Docker 镜像 / 容器     本地审计后复用
  Harbor                 确认位置、接口、权限后接入
  原始轨迹               检查字段后用于分析或训练
  Checkpoint / Restore   现有能力不足则在新项目实现
  Branch Runner          在新项目实现统一接口
  Qwen3-8B               远程部署，本地 HTTPS 调用

## 4. 算法设计

### 4.1 风险预测

\[ p_t=P(Y\_{`\mathrm{fail}`{=tex}}=1`\mid `{=tex}X\_{`\leq `{=tex}t})
\]

标签定义为预先指定基准策略（初版
Continue）下任务最终是否失败。特征只允许使用当前及历史信息。

特征包括执行进度、工具调用次数、错误次数、重复行为、无进展步数、近期延迟和基于历史窗口计算的工具成功率。

模型： - 基线：Logistic Regression； - 主模型：LightGBM。

指标：AUROC、AUPRC、Brier Score、Precision、Recall、校准曲线。

禁止使用未来工具调用、最终答案、分支结果和未来成本作为当前特征。

### 4.2 干预效用估计

\[ A={CONTINUE, RETRY, REPLAN} \]

\[ `\mu`{=tex}\_a(x)=P(Y=1`\mid `{=tex}X=x,A=a) \]

第一版采用 T-Learner，为每个动作训练独立结果模型。成本：

\[
C(a,x)=w_1C\_{`\mathrm{token}`{=tex}}+w_2C\_{`\mathrm{tool}`{=tex}}+w_3C\_{`\mathrm{latency}`{=tex}}
\]

相对 Continue 的净效用：

\[
U(a`\mid `{=tex}x)=`\mu`{=tex}*a(x)-`\mu`{=tex}*{`\mathrm{continue}`{=tex}}(x)
-`\lambda[C(a,x)-C(\mathrm{continue},x)]`{=tex}\]

权重只通过训练集/验证集选择，测试集只做最终评估。不适用或不安全动作必须显式标记。离线预测不自动等于无偏因果效应，需报告动作覆盖和数据限制。

### 4.3 自适应策略

1.  检查安全约束和预算。
2.  预测失败风险。
3.  低风险时优先 Continue。
4.  高风险时估计可行动作的成功概率和成本。
5.  排除不安全或不适用动作。
6.  选择净效用最高且超过收益门槛的动作。
7.  无动作达到门槛时按安全规则 Continue 或 Stop。
8.  限制 Retry / Replan 次数，检测循环。

## 5. 任务与数据

### 四个领域

  ---------------------------------------------------------------------------------------------
  领域                    工具示例                                      任务
  ----------------------- --------------------------------------------- -----------------------
  Data Analysis           read_csv、filter_data、aggregate_data         过滤、统计、聚合

  File Retrieval          list_files、search_files、read_file           文件定位、信息抽取

  API Workflow            query_inventory、get_order、update_order      查询和状态更新

  Calculation & Logic     calculator、unit_converter、validate_result   多步计算和判断
  ---------------------------------------------------------------------------------------------

所有任务必须有确定性验证器，不能只依靠 LLM Judge。

### 故障类型

F0 No Fault；F1 Timeout；F2 API 503；F3 Invalid Argument；F4 Stale
Observation；F5 Repeated Action / No Progress；F6 Partial Failure。

Partial Failure 必须区分操作是否提交和客户端是否收到成功响应。

### 规模目标

-   40 个模板，每领域 10 个；
-   每模板 5 个实例，目标 200 个实例；
-   最多 200 个检查点，每检查点最多 3 个候选分支。

先用 8--20 个任务和至少 5 个检查点验证全链路。若时间不足，优先保证至少
30 个高质量检查点，不得为凑数量牺牲状态一致性。

### 数据划分

按模板分组：Train 24 个模板 / 120 实例；Validation 8 / 40；Test 8 /
40。同一模板实例和同一 Checkpoint 的所有分支必须在同一 Split。

### 轨迹字段

记录
`experiment_id`、`task_id`、`template_id`、`instance_id`、`seed`、`model_id`、`model_version`、`prompt_version`、`tool_version`、`step_id`、`tool_name`、`tool_arguments`、`tool_result`、`error_type`、`timestamp`、`token_usage`、`latency`、`worker_id`、`environment_id`、`checkpoint_id`。

分支额外记录
`branch_id`、`action`、`action_applicable`、`success`、`validation_passed`、额外
Token / Tool Calls / Latency、终止原因、初始和恢复状态哈希。

原始数据只追加不覆盖；派生数据必须追溯到脚本、配置和数据版本。

## 6. Checkpoint / Restore / Branch 协议

快照根据环境保存 Agent Memory、工具调用历史、任务文件、SQLite（使用
SQLite Backup API）、Mock API 状态、工具状态、预算计数器、任务配置和
seed。

`docker commit`
不等于完整运行时快照。第一版优先保存任务级文件、数据库、Mock API 和
Agent Memory，不要求 CRIU。

每个分支从同一逻辑状态恢复，使用独立可变文件、数据库和服务状态，不共享可变
Volume，保持相同模型、Prompt
和推理参数，使用同一验证器。通过规范化状态清单哈希检查 Agent
Memory、任务目录、数据库、Mock API、工具状态、配置和预算。

## 7. 仓库结构

``` text
risk-aware-smolagents/
├── AGENTS.md
├── PROJECT_GUIDE.md
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── configs/
│   ├── local.yaml
│   ├── remote_model.yaml
│   ├── agent.yaml
│   ├── tasks.yaml
│   ├── faults.yaml
│   └── experiment.yaml
├── src/risk_agent/
│   ├── agent/ (agent_factory.py, worker_router.py)
│   ├── tools/
│   ├── monitoring/
│   ├── prediction/
│   ├── controller/
│   ├── benchmark/
│   ├── training/
│   └── evaluation/
├── data/
├── models/ (仅风险模型和效用模型，不放 Qwen 权重)
├── scripts/
├── tests/
├── runs/
└── docs/
    ├── daily_reports/
    ├── environment_audit.md
    ├── recovery_bench_reuse.md
    └── day1_architecture.md
```

按实际实现调整目录，不要创建无功能空壳文件。

## 8. 配置与 Worker Router

本地配置 `configs/local.yaml` 管理本地 Recovery-Bench
路径、Docker、任务数据和日志。远程配置 `configs/remote_model.yaml`
管理两个 Worker 的 Base URL、模型 ID、超时和 TLS CA Bundle 路径。

API Key 只从环境变量或安全密钥管理读取，不写入 YAML 或 Git。

Worker Router： - 支持显式选择 Worker 0 / 1、轮询或负载策略； -
每次请求记录 `worker_id`； - 失败时按明确策略重试或切换； -
不因模型请求重试而重复执行已有副作用的工具操作； - 两个 Worker
的模型版本、Prompt 和推理参数必须可追踪。

## 9. 14 天实施计划

### Day 1 --- 本地与远程环境审计、仓库初始化

**本地审计：** 1. 检查 Recovery-Bench 路径、Git
状态、`pipeline.py`、`per_task.json`、Agent 执行入口。 2. 检查 Docker
镜像、容器、Volumes、Mounts。 3. 检查 Harbor
实际位置、Endpoint、任务接口和日志。 4.
检查逐步工具轨迹、验证器、reset、Checkpoint、Restore 和分支能力。 5.
不修改原 Recovery-Bench，不删除或重建容器。

**远程审计（SSH 已配置时）：** 1. 宿主/容器边界、Ubuntu、CPU、内存、磁盘； 2. 八张共享 RTX
4090 的可见性、占用、Driver、CUDA，并确认两张可用空闲卡； 3. Conda、Python； 4. vLLM 当前状态； 5.
Qwen3-8B 权重是否存在； 6. 8000 / 8001 端口； 7. 模型下载和 TLS
部署条件； 8. 本地到远程 SSH / HTTPS 连通性。

若 SSH 不可用，不猜测连接参数；生成检查命令请用户执行。

**产出：** - `docs/environment_audit.md` -
`docs/recovery_bench_reuse.md` - `docs/day1_architecture.md` - Git
仓库初始化和 `.gitignore`

**验收：** 本地与远程分开审计；Harbor
位置明确或标记待确认；复用结论有代码证据；远程 GPU
信息有命令输出；原环境未被破坏。

**Gate：** 未确认本地环境和远程连接条件，不进入联调。

### Day 2 --- 本地 smolagents 接入远程 Qwen3-8B

1.  在本地 WSL 创建独立 Conda 环境。
2.  安装 smolagents 和 OpenAI-compatible 客户端。
3.  配置远程 HTTPS API。
4.  分别检查两个 Endpoint 的 `/v1/models`。
5.  每个 Endpoint 完成至少 5 次普通对话。
6.  实现安全 CalculatorTool，正式代码禁止不受限制的 `eval()`。
7.  验证模型 Tool Call 由本地 smolagents 执行。
8.  记录 Worker ID、工具名、参数、结果、Token 和延迟。
9.  测试超时、TLS 错误和服务不可用处理。

**产出：** - `src/risk_agent/agent/agent_factory.py` -
`src/risk_agent/agent/worker_router.py` -
`src/risk_agent/tools/calculation_tools.py` -
`scripts/check_remote_api.py` - `scripts/smoke_test.py` -
`tests/test_agent.py`

**验收：** 两个 Endpoint 可访问；每个 Endpoint 至少 5 次对话；至少 10
次工具调用测试成功不少于 9
次；工具在本地执行；轨迹结构化保存；密钥不进日志；TLS 校验开启。

若远程 vLLM 尚未部署，先完成客户端和 Mock Endpoint 测试；Mock
测试不能标记为真实模型通过。模型下载和部署须按资源操作规范确认后执行。

### Day 3 --- 四类本地任务环境

实现四领域工具、YAML Task Loader、环境 reset 和确定性验证器。每领域至少
2 个任务。验收：至少 8 个端到端任务；全部有验证器；reset
有效；写操作安全。

### Day 4 --- 故障注入

实现 F0--F6、固定 seed、故障触发日志和动作安全检查。验收：按配置触发、同
seed 可复现、Partial Failure 状态可区分、不安全 Retry 可阻止。

### Day 5 --- 轨迹监控与 Checkpoint

记录工具调用、参数、结果、错误、Token、延迟；保存 Agent
Memory、文件、SQLite、Mock API
和预算状态。验收：快照覆盖必要状态、恢复后规范化哈希一致、SQLite
备份正确。

**Gate：** Checkpoint / Restore 未通过，不开始正式分支采集。

### Day 6 --- 独立分支实验

实现 Continue、Retry、Replan；每个分支从独立副本恢复。验收：至少 5
个三个动作均适用的检查点完成三分支；初始哈希一致；无可变状态串扰；结果经验证器判定。

### Day 7 --- 任务模板与实例

四领域各 10 个模板，每模板目标 5 个实例；按模板分组划分数据。验收：目标
40 模板、200 实例，全部有验证器，可按 seed 复现。

### Day 8 --- 初始轨迹采集

本地 smolagents 调用远程模型，执行本地任务；双 Worker 记录独立任务 ID 和
Worker ID。验收：任务 ID 唯一、两个 Endpoint
有真实请求、服务故障与任务失败分开、日志完整率目标 ≥95%。

### Day 9 --- 干预分支与效用数据

对有效检查点执行 Continue 和适用的 Retry / Replan。验收：每个检查点有
Continue 结果；不适用动作有原因；成本字段完整；分支不跨 Split。

### Day 10 --- 风险预测

训练 Logistic Regression 和
LightGBM。验收：无未来泄漏；模板级隔离；测试集不调参；报告
AUROC、AUPRC、Brier、Precision、Recall。

### Day 11 --- 效用估计

训练 Continue、Retry、Replan 结果模型和成本模型。验收：概率在
\[0,1\]、成本非负、不适用动作处理正确；报告 Outcome Brier / MAE、Cost
MAE、Utility MAE、Policy Regret；测试集不调权重。

### Day 12 --- 自适应策略

集成风险、效用、安全约束和预算。验收：低风险
Continue、高风险比较动作、不安全 Retry
阻止、无无限重试、模型异常安全回退，至少 20 个端到端任务通过。

### Day 13 --- 基线、消融和主实验

基线：Vanilla Agent、Always
Retry（仅安全适用）、Rule-based、Risk-based、Risk +
Utility。消融：Full、w/o Risk、w/o Utility、w/o
Cost。报告成功率、恢复率、干预率、不必要干预率、Token、Latency、Tool
Calls、Regret，并按领域和故障类型分层。

### Day 14 --- Recovery-Bench 扩展验证与交付

经 Adapter 选择少量本地 Recovery-Bench
任务。只有实际支持一致状态恢复时才做分支效用实验，否则报告限制。完成复现文档、最终报告、README
和 Demo。

## 10. 评价指标与实验有效性

指标： - 可靠性：Task Success Rate、Recovery Rate、Failure
Rate、Validation Pass Rate； - 干预：Intervention Rate、Unnecessary
Intervention Rate、Retry / Replan / Stop Rate； - 成本：Average
Tokens、Average Latency、Average Tool Calls、Recovery Cost； -
风险：AUROC、AUPRC、Brier、Precision、Recall、Calibration； -
效用：Outcome Brier / MAE、Cost MAE、Utility MAE、Policy Regret。

所有指标说明分母和口径，并按领域、故障类型分层。报告均值和标准差或置信区间，相关分支采用配对分析。区分实测结果、估计结果和示例结果。

远程 API 错误不能直接记为 Agent
任务失败；服务不可用期间未完成的任务应标记为 infra/API
failure，并按协议恢复。

## 11. 每日报告与交付规范

日报保存到 `docs/daily_reports/day_XX_report.md`：

``` markdown
# Day XX Report
## Objectives
## Local Environment Work
## Remote Environment Work
## Work Completed
## Files Changed
## Artifacts Generated
## Commands and Tests
## Test Results
## Acceptance Checklist
## Known Issues
## Remaining Work
## Status: PASS / CONDITIONAL PASS / BLOCKED
## Git Commit
```

最终交付： - 本地 smolagents Agent 和 Worker Router； - 远程双 vLLM
Endpoint 客户端； - 四领域任务、故障注入、轨迹监控； - Checkpoint /
Restore / Branch Runner； - 风险预测、效用估计、自适应策略； - Risk /
Utility 数据集； - 基线、消融和分层报告； - README、复现指南、最终报告和
Demo。

## 12. Codex 执行约定

本文件是项目指南，不是一次性执行全部任务的 Prompt。每次会话只做当前
Day；先明确本地与远程分别执行什么；先审计再修改；运行测试并生成日报；汇报
PASS / CONDITIONAL PASS / BLOCKED；等待用户确认后再进入下一天。
