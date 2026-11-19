# MiniToolBench-R 下一阶段实验执行指南（Codex 版）

> 版本：1.0\
> 日期：2026-11-19\
> 主工作区：本地 WSL2 Ubuntu
> 24.04，`/home/three/projects/risk-aware-smolagents`\
> 远程职责：只运行 Qwen3-8B / vLLM
> 推理服务；任务环境、Checkpoint、分支、Verifier、训练和分析均在本地
> WSL。

## 0. 指南目标与 Codex 执行规则

本指南不重新搭建项目，也不推翻 Day 1--14。下一阶段分为三阶段：

1.  **阶段一：审计现有实验，修正数据与实验协议。**
2.  **阶段二：采集真实 Qwen3-8B 中间状态和同状态干预分支。**
3.  **阶段三：重训风险/效用模型，完成策略对比和独立评估。**

核心研究问题：

> Agent 执行过程中，能否根据当前 Checkpoint
> 已经可观测的状态和历史信息，预测继续执行的失败风险、估计不同干预动作的条件结果与成本，并选择更合适的干预动作？

每次 Codex 会话必须遵守：

-   先阅读
    `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`、本指南和当前阶段日报。
-   一次只执行用户指定的一个日任务，例如 `P1-D1`；不得自行连续推进。
-   开始前检查 `pwd`、`git status --short`、当前配置、历史 run
    和相关产物。
-   先给出本日计划、预计修改文件、风险和验收标准，再开始操作。
-   每日结束生成日报，运行专项测试和全量回归，明确报告 PASS /
    CONDITIONAL PASS / BLOCKED。
-   Gate 未通过就停止，不得跳过检查或降低标准。
-   工程 Gate PASS 不等于研究结论成立。
-   历史 run、模型、日志、Checkpoint、JSONL
    和日报一律只追加、不覆盖、不删除。
-   未经用户明确批准，不启动远程 GPU、不运行大批量采集、不启动新的
    Harbor/Docker Trial。
-   不确定的路径、字段、命令或 API 必须先检查实际仓库，不允许猜测。

------------------------------------------------------------------------

## 1. 当前项目基线（必须如实继承）

以下事实来自现有 `01to14_report(1).md`。本阶段不得将本地 Reference
Runner 结果改写为真实 Qwen 结果。

### 1.1 环境与职责

-   主项目：`/home/three/projects/risk-aware-smolagents`，本地 WSL2
    Ubuntu 24.04。
-   本地 Python 环境：`.venv`，优先使用 `.venv/bin/python`。
-   远程模型：Qwen3-8B，通过 vLLM OpenAI-compatible HTTPS Endpoint
    提供服务。
-   远程服务器有 8 张共享 RTX 4090；每次最多使用 2
    张，必须动态检查空闲状态，不能假设 GPU 0/1 或其他历史编号仍空闲。
-   本地负责 Agent
    harness、任务环境、工具、Checkpoint、分支编排、Verifier、采集、训练和分析。
-   远程只负责模型推理。复用已有 vLLM 环境、权重、TLS、API Key 和
    `scripts/manage_remote_vllm.py`，不要重新安装或下载。
-   外部 Recovery-Bench 位于
    `/home/three/projects/agent-intervention/recovery-bench`，有大量历史未提交修改，必须只读保护。

### 1.2 Day 7 任务目录

-   4 个领域：Data Analysis、File Retrieval、API Workflow、Calculation &
    Logic。
-   40 个模板，每模板 5 个实例，共 200 个实例。
-   模板级 Train / Validation / Test = 24 / 8 / 8；实例级 = 120 / 40 /
    40。
-   每个领域均为 6 / 2 / 2 个模板。
-   原 catalog
    hash：`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`。
-   200/200 Reference Runner 成功只证明任务环境和 Verifier
    可运行，不代表 Qwen3-8B 能自主完成 200 个任务。

### 1.3 Day 8：真实 Qwen 初始轨迹

-   只采集了 24 个 Train 模板各一个实例。
-   24 个逻辑任务：19 success、4 infra_failure、1 task_failure。
-   保存 43 条原始 attempt、26 次本地工具调用。
-   任务日志完整率 99.28%，工具日志完整率 95.24%。
-   4 个 infra_failure 必须与 Agent task_failure 区分。
-   Day 8 没有保存可直接用于同状态分支的逐步
    Checkpoint，因此不能冒充真实中间状态干预数据。

### 1.4 Day 9：Reference Branch 数据

-   从 Day 8 的 20 个非 infra 任务构造 20 个 Train Checkpoint。
-   60 条候选动作记录；实际适用 36 条：Continue 20、Retry 6、Replan 10。
-   适用分支 27 成功、9 失败；失败集中在 Continue 对照。
-   分支由 `deterministic_local_reference` 执行，不是远程 Qwen3-8B
    自主执行。
-   20 个 Checkpoint 全部属于 Train。
-   Day 9 Checkpoint 是 fresh pre-tool snapshot，不是 Day 8
    进程内状态的事后恢复。

### 1.5 Day 10：风险模型

-   20 个 Train Checkpoint，9 failure / 11 success，26 个数值特征。
-   Logistic Regression、LightGBM、5 折 `StratifiedGroupKFold`，按
    `template_id` 分组。
-   当前 OOF AUROC/AUPRC 均为 1.0，但每折只有 4 个验证样本。
-   `failure_response_received`
    几乎直接区分当前人工构造故障场景；LightGBM 的 split importance
    全部落在该特征。
-   当前满分不能证明真实 Agent 风险泛化；没有官方 Validation/Test
    Checkpoint。

### 1.6 Day 11：动作效用与成本模型

-   20 个 Checkpoint、60 条候选动作、36 条适用动作。
-   Continue：20 条，11 success / 9 failure。
-   Retry：6 条，6 success / 0 failure。
-   Replan：10 条，10 success / 0 failure。
-   Retry、Replan 没有负类，当前只能使用 Beta
    平滑常数模型，不能学习状态条件差异。
-   额外模型 Token 全为 0；Latency 是本地轻量工具时间，不代表真实 LLM
    干预成本。
-   `w_token=0.001`、`w_tool=1`、`w_latency=1`、`lambda=0.1`
    是工程默认值，不是验证得到的最优权重。

### 1.7 Day 12--13：策略集成

-   Day 12 在 20 个 Train Checkpoint 上完成本地策略集成和安全探针。
-   Day 13 完成 9 个策略 × 20 个 Checkpoint = 180 条本地策略执行记录。
-   Vanilla 成功率 55%；Rule-based、Risk-based、Risk + Utility、Full
    和消融均为 100%。
-   Risk-based、Risk + Utility、Full、w/o Risk、w/o Utility、w/o Cost
    在当前样本上的离散动作完全相同。
-   Risk + Utility 和 Full 当前决策逻辑重复，不能证明 Full 优于消融。
-   Day 13 没有调用远程 Qwen3-8B，Token 为 0；全部 180 条来自 Train。
-   100% 成功率是 20 个同源 Train Checkpoint
    上的工程结果，不是独立泛化结果；没有置信区间或显著性检验。

### 1.8 Day 14：Recovery-Bench Adapter

-   只读解析 3 个历史 Harbor
    Trial：success、verifier_failure、agent_error 各 1 个。
-   标准化 37 个 ATIF steps、100 个 tool calls、34 个 observations。
-   Recovery-Bench command replay 是 best-effort replay，不是完整
    Checkpoint / Restore。
-   已明确
    `checkpoint_restore_supported=false`、`branch_utility_experiment_executed=false`。
-   不得把 Day 14 只读 Adapter 验收描述为 Recovery-Bench
    同状态干预实验。
-   外部 Recovery-Bench 仓库禁止 reset、clean、checkout、format、commit
    或修改。

Day 14 最终全量回归为 76 passed。新阶段必须保持旧功能兼容。

------------------------------------------------------------------------

## 2. 统一研究定义

### 2.1 决策时刻

决策时刻是：Agent
已经收到当前工具反馈/错误响应，但尚未进行下一次模型推理或工具操作的时刻。必须在此时保存
Checkpoint。

Checkpoint 至少包含：

-   Agent messages / 对话历史；
-   Agent Memory、工具调用历史；
-   当前 pending operation、最新工具响应；
-   文件系统、SQLite、Mock API、工具状态；
-   预算状态；
-   template/task/instance/seed/split；
-   模型 ID、Worker、generation config；
-   当前 step 和事件信息；
-   规范化组合状态 hash。

不能把最终状态、未来分支结果、Verifier 隐藏答案或未来成本放进决策特征。

### 2.2 三个动作的固定语义

**CONTINUE** - 从 Checkpoint 恢复完整 Agent 对话与环境状态。 -
不添加干预指令，按原流程继续响应当前工具反馈。

**RETRY** - 只重试安全且适用的失败操作。 - 只读操作按协议重试。 -
写操作必须满足 `not_committed` 且具有幂等键。 - `committed` 或 `unknown`
不允许直接重试；状态不确定时先查询。 - 每个 Checkpoint 最多 Retry
一次，禁止循环。

**REPLAN** - 从同一 Checkpoint 恢复。 - 只向 Qwen
提供当前已知上下文和工具反馈，要求生成修正计划。 - stale observation
等情况先做允许的状态刷新。 - 不得暴露隐藏答案、未来分支结果或 Oracle
信息。 - 每个 Checkpoint 最多 Replan 一次，禁止循环。

不适用动作必须记录 `not_applicable`
和原因，不能记为失败或零成本训练样本。

### 2.3 风险、结果和成本

对 Checkpoint 特征 (x)：

\[ r(x)=P(Y\_{`\mathrm{continue}`{=tex}}=0`\mid `{=tex}x) \]

其中 `Y_continue=1` 表示 Continue 分支通过确定性 Verifier。

对动作 (a)：

\[ p_a(x)=P(Y_a=1`\mid `{=tex}x,a,`\mathrm{applicable}`{=tex}) \]

成本至少拆成 Prompt Tokens、Completion Tokens、Tool Calls、Model
Latency、Tool Latency、Total Wall-clock Latency。

-   `null` 表示不可观测，不能用 0 替代。
-   0 只表示真实没有发生该项成本。
-   成功/失败由 `DeterministicVerifier` 判定，不能使用模型自评。
-   内部 fault metadata 与 Agent 实际可见的 tool response 必须分开存储。

### 2.4 数据来源标记

每条数据都必须记录
`controller_type`、`execution_mode`、`split`、`catalog_version`、`template_id`、`task_id`、`checkpoint_id`、`branch_id`。

正式风险/效用主实验必须使用真实 Qwen3-8B 轨迹和模型驱动分支。历史
Reference
数据只作为工程对照、单元测试和独立补充分析，不得混合后统一宣称为真实模型效果。

------------------------------------------------------------------------

## 3. 不可违反的边界

### 3.1 本地/远程

-   Codex 工作目录始终是本地 WSL 项目，不要把整个项目迁移到远程容器。
-   本地负责任务环境、工具、Checkpoint、Branch、Verifier、数据处理和训练。
-   远程只提供 Qwen3-8B 推理 API。
-   复用已有远程环境和管理脚本；不重新下载模型，不改动已有 `agent`
    环境。

### 3.2 共享 GPU 安全

每次启动前重新检查全部 8 张 GPU：

1.  连续两次采样确认空闲；
2.  确认无其他 compute process；
3.  最多选择两张空闲卡；
4.  记录 Worker 到物理 GPU 映射；
5.  没有合规空闲卡就停止，不抢占；
6.  SSH 超时后先查 PID、端口和 Worker 状态，不得盲目重复启动；
7.  只停止状态文件和命令行双重确认属于本次 run 的 PID；
8.  结束后检查 Worker、隧道、端口和 GPU 资源释放。

不得假设 GPU 0/1 仍空闲，不得关闭其他用户进程。

### 3.3 仓库和数据保护

-   禁止 `git reset --hard`、`git clean -fdx`、批量删除或覆盖历史产物。
-   不删除 Day 1--14 的 run、模型、日志、Checkpoint 和日报。
-   新阶段使用新配置和新 run 目录，建议 `phase2_*`、`phase3_*`。
-   先检查 `git status --short`；不能假设当前仓库有干净 Git 基线。
-   未经用户要求不创建 commit。
-   外部 Recovery-Bench 仓库只读。
-   API Key、SSH 私钥、证书私钥、密码不得进入代码、配置、日报和轨迹。
-   原始模型消息和工具参数需要脱敏；保留可审计摘要/hash。

### 3.4 防止数据泄漏

-   Train / Validation / Test 必须按 `template_id` 分组。
-   同一模板所有实例、Checkpoint、动作分支只能属于一个 split。
-   同一 Checkpoint 的所有动作分支必须属于同一 split。
-   风险特征只能使用决策时刻已经可观测的信息。
-   `fault_type`、`injection_seed`、内部注入配置、expected
    answer、Verifier 隐藏字段、未来工具结果默认禁止作为部署型风险特征。
-   如果错误类别已经通过工具响应暴露给
    Agent，可以作为可观测响应特征，但必须与内部故障标签分开保存并做消融。
-   Test 不得用于调特征、模型、阈值、成本权重或策略。
-   Test 评估前只允许检查 schema、完整性、缺失值、split 和
    hash，不允许按 Test 标签调参。

------------------------------------------------------------------------

## 4. 三阶段总体计划

  -----------------------------------------------------------------------------------------
  阶段                     日任务                  主要产出
  ------------------------ ----------------------- ----------------------------------------
  阶段一：审计与协议修正   P1-D1 至 P1-D4          历史审计、特征审计、策略审计、Protocol
                                                   v2

  阶段二：真实数据扩充     P2-D1 至 P2-D8          新模板、模型
                                                   Checkpoint、真实分支、冻结数据集

  阶段三：训练与独立评估   P3-D1 至 P3-D6          风险模型、效用模型、策略比较、Test 报告
  -----------------------------------------------------------------------------------------

预计 3--5 周，取决于 Codex 开发、远程 GPU
空闲情况和真实轨迹采集效率。不得为压缩时间跳过 Gate。

# 5. 阶段一：现有实验质量审计与协议修正

## P1-D1：历史实验与产物基线审计

**目标：** 建立不可变的历史实验清单，确认正式
run、配置、模型、数据和日报的来源关系。

**步骤：**

1.  确认 `pwd` 位于本地项目根目录，记录 `git status --short`。
2.  读取 `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`。
3.  列出 Day 7--14 正式 run 路径、run ID、配置、summary 和关键数据产物。
4.  对关键输入生成 SHA-256 manifest。
5.  检查重复 task/branch ID、缺少 Checkpoint hash、缺少 split、缺少
    controller_type。
6.  核对 Day 8 的 24 个逻辑任务与 43 个 attempts。
7.  核对 Day 9 的 20 Checkpoint、60 action records 和动作适用性。
8.  核对 Day 10/11 模型元数据中的训练来源和 feature schema。
9.  核对 Day 13 的 180 条策略记录和 180 条分支结果是否一一对应。
10. 不修改历史产物，不重跑历史正式实验。

**输出：** - `docs/phase2/audit_inventory.md` -
`docs/phase2/historical_artifact_manifest.json` -
`docs/phase2/daily_reports/p1_d1_report.md`

**Gate：** 每个关键 run 和模型都可定位；SHA-256
可复核；缺失项逐项记录；旧产物未修改。

------------------------------------------------------------------------

## P1-D2：风险预测特征捷径审计

**目标：** 查明 Day 10
满分是否主要由人工故障构造特征造成，并确定部署型风险特征 allowlist。

**步骤：**

1.  阅读风险特征提取代码，逐项列出 26
    个特征的名称、含义、来源和可观测时间。
2.  将特征分组：
    -   `agent_history`：历史工具调用、历史错误、进度、重复动作；
    -   `observed_tool_response`：决策时刻真实可见的工具响应；
    -   `injection_metadata`：仅实验控制器知道的故障类型、注入阶段、seed；
    -   `future_oracle`：未来分支结果、Verifier
        隐藏信息、未来成本和未来工具结果。
3.  每个特征必须注明 `allowed_for_deployment`，不能只按字段名推断。
4.  使用相同 template-grouped folds 完成：
    -   `all_current_features`；
    -   `without_failure_response_received`；
    -   `history_only`；
    -   `observed_response_only`；
    -   `injection_metadata_only`（仅诊断捷径，不进入部署模型）；
    -   `deployment_allowlist_only`。
5.  报告 AUROC、AUPRC、Brier、Log Loss、Precision、Recall 和校准曲线。
6.  加入正类率常数预测和 Logistic Regression 基线。
7.  某折只有单类别时，该折指标记为不可定义，不得伪造 0 或 1。
8.  使用分组消融或置换重要性；小样本下不能把单次 split importance
    当成稳定解释。
9.  只用当前 Train OOF 数据，不访问官方 Test。

**输出：** - `docs/phase2/risk_feature_audit.md` -
`runs/phase2_audit/risk_feature_ablation.json` -
`runs/phase2_audit/risk_feature_ablation.csv` -
`runs/phase2_audit/risk_oof_predictions.jsonl` -
`docs/phase2/daily_reports/p1_d2_report.md`

**Gate：** 26 个特征全部有来源和可观测性说明；满分被标注为小样本 Train
OOF；形成部署 allowlist；注入 metadata 不进入正式风险模型。

------------------------------------------------------------------------

## P1-D3：效用、成本与策略等价性审计

**目标：** 查明 Day 11 动作标签覆盖不足、成本不可观测和 Day 13
策略重复问题。

**步骤：**

1.  从 Day 9 `branch_outcomes.jsonl`
    重新统计每个动作的候选数、适用数、不适用原因、success/failure、Token/Tool
    Calls/Latency 缺失率。
2.  分别统计 Continue、Retry、Replan 的成功和失败类别。
3.  明确 Retry 6/6、Replan 10/10 全成功导致模型无法学习负类。
4.  检查 Day 11 的 outcome 模型、Beta fallback、OOF 预测和成本模型。
5.  Token=0 只能解释为真实没有模型调用，不能解释为成本低。
6.  读取 Day 13 策略工厂和决策日志，逐 Checkpoint
    比较动作、风险门控、utility 排序、cost 影响、Full 与 Risk + Utility
    代码路径和消融关闭情况。
7.  生成策略动作一致性矩阵和逐 Checkpoint 差异清单。
8.  对不适用动作保留 `not_applicable`；不填失败、成功或零成本。

**输出：** - `docs/phase2/utility_policy_audit.md` -
`runs/phase2_audit/action_coverage.json` -
`runs/phase2_audit/policy_action_equivalence.csv` -
`runs/phase2_audit/cost_observability.json` -
`docs/phase2/daily_reports/p1_d3_report.md`

**Gate：** 动作适用性与结果标签严格区分；Retry/Replan
单类别问题量化；Token 缺失与真实 0 区分；策略代码路径和数学定义清楚。

------------------------------------------------------------------------

## P1-D4：冻结 Protocol v2 与数据 Schema

**目标：**
在新数据采集前冻结任务扩充、决策时刻、动作语义、数据划分、指标和停止条件。

### 任务模板方案

不覆盖原 Day 7 catalog：

-   原有 40 个模板、ID、参数、seed 和 split 保持不变。
-   新增 40 个模板，每个领域新增 10 个。
-   新增模板每领域分配 6 Train、2 Validation、2 Test。
-   v2 总计 80 个模板：48 Train、16 Validation、16 Test。
-   每个模板采集 2 个实例 seed，计划 160 个真实 Qwen 任务执行。
-   新增模板以 3--8
    个有依赖关系的工具步骤为主，不能只把单步操作人为拆开。
-   四个领域保持平衡，覆盖简单、常规和多步骤依赖。

新增模板方向：

-   Data Analysis：读取 → 筛选 → 聚合 → 计算 → 输出/验证。
-   File Retrieval：定位 → 搜索 → 读取 → 交叉核对 → 输出。
-   API Workflow：查询 → 判断 → 更新 → 查询验证；写操作必须使用幂等键。
-   Calculation & Logic：读取输入 → 多步计算 → 结果校验 → 汇总/写入。

### 故障矩阵

继续使用 F0--F6，并验证每种故障在新工具环境中的真实触发语义：

  故障   含义                            主要观测
  ------ ------------------------------- -------------------------------------
  F0     No Fault                        正常中间状态
  F1     Timeout                         超时反馈、提交状态
  F2     API 503                         临时服务不可用
  F3     Invalid Argument                参数错误响应
  F4     Stale Observation               过期状态或版本冲突
  F5     Repeated Action / No Progress   重复调用、无进展
  F6     Partial Failure                 committed / not_committed / unknown

要求：

-   每个 split 尽量覆盖 F0--F6；输出 Domain × Fault × Split 覆盖矩阵。
-   故障注入只控制环境响应，不直接把隐藏标签写进模型输入。
-   工具实际响应、内部注入 metadata、Verifier 标签分开存储。
-   F6 的三种提交状态分别记录；`unknown` 不得直接 Retry。
-   不要求每个 Domain × Fault 组合都存在，但缺失格必须说明。

### 预注册数据目标

  数据项                             最低 Gate   目标
  -------------------------------- ----------- ------
  总模板                                    80     80
  Qwen 任务执行                            120    160
  有效真实决策 Checkpoint                  120    160
  Train Checkpoint                          72     96
  Validation Checkpoint                     24     32
  Test Checkpoint                           24     32
  Retry 适用 Train 结果                     30    40+
  Replan 适用 Train 结果                    30    40+
  Retry/Replan 每动作 Train 正类            10    15+
  Retry/Replan 每动作 Train 负类            10    15+

Checkpoint 数量按唯一决策状态计数，不把同一 Checkpoint
的多个动作分支重复算成多个状态。目标不是凑标签；成功/失败必须由真实分支和
Verifier 产生。若某动作样本或类别不足，只扩充
Train，不伪造失败。Validation/Test 覆盖不足时如实报告。

至少 20% 的 Checkpoint-action
组合做独立重复运行，用于估计随机性；重复记录不计入唯一 Checkpoint 数量。

**输出：** - `docs/phase2/protocol_v2.md` -
`configs/task_templates_v2.yaml` - `configs/phase2_collection.yaml` -
`docs/phase2/schema_v2.md` - `docs/phase2/daily_reports/p1_d4_report.md`

**Gate：** 用户确认 Protocol v2
后才允许正式批量采集；split、决策时刻、动作、标签、成本和指标全部冻结；新配置不覆盖
Day 7--14 文件。

------------------------------------------------------------------------

# 6. 阶段二：真实 Qwen 中间状态与同状态分支数据扩充

## P2-D1：新增多步骤模板与故障覆盖矩阵

**目标：** 新增 40 个多步骤模板，完成 80 模板 v2 catalog。

**步骤：**

1.  阅读原 `configs/task_templates.yaml` 和 Day 7 生成器。
2.  保留原 40 个模板 ID、参数、seed 和 split；创建 v2 配置。
3.  每领域新增 10 个模板，其中 6 Train、2 Validation、2 Test。
4.  每个新增模板至少 3 个有实际依赖的工具步骤，目标 3--8 步。
5.  每个任务配置 reference steps、expected
    checks、故障位置和动作适用性条件。
6.  设计 F0--F6 确定性 fault schedule，尽量平衡 split 和 domain。
7.  用 Reference Runner 验证新任务环境和 Verifier；Reference
    成功只作为环境 Gate。
8.  生成 catalog v2 hash 和 split manifest。

**输出：**
`configs/task_templates_v2.yaml`、`runs/phase2_catalog/<run_id>/task_catalog.json`、`split_manifest.json`、`fault_coverage_matrix.json`、`reference_validation.json`、日报。

**Gate：** 80 个唯一模板，4 个领域各 20；Train/Validation/Test =
48/16/16；新增 40 个模板通过
Verifier；新模板不是只改文本的重复任务；覆盖矩阵完整。

------------------------------------------------------------------------

## P2-D2：实现真实决策时刻 Checkpoint 捕获

**目标：** 让真实 Qwen Agent 在执行过程中保存可恢复的决策状态。

**步骤：**

1.  阅读当前 smolagents 版本、Agent Factory、Tool
    Adapter、ExecutionMonitor 和 RuntimeState。
2.  找到真实 Agent messages、memory、tool calls、tool responses
    的生命周期节点。
3.  使用受支持 callback、subclass 或明确封装的执行循环实现
    hook；不得未经验证 monkey-patch 私有 API。
4.  捕获时机必须是：工具响应已经返回
    Agent，错误/状态反馈已进入上下文，但下一次模型推理和工具操作尚未发生。
5.  保存模型可恢复上下文、任务环境、工具状态、预算和元数据。
6.  对文件、SQLite、Mock API、RuntimeState、消息历史计算规范化 hash。
7.  Restore 到新工作区后重新计算 combined hash，必须与 snapshot 一致。
8.  每个 Checkpoint 保存 manifest 和内容 checksum。
9.  若无法无损保存 Agent 上下文，先实现最小适配和测试；不能只保存 prompt
    摘要后声称可恢复。

Checkpoint 至少包含：

-   `checkpoint_id`、`source_task_run_id`、`template_id`、`task_id`、`instance_id`、`split`；
-   `catalog_hash`、`model_id`、`worker_id`、`generation_config`、`seed`、`step_index`；
-   messages、agent_memory、tool_history、pending_operation、latest_tool_response；
-   runtime_state、task_environment_manifest、file_checksums、sqlite_checksum、mock_api_state_hash；
-   environment_hash、combined_state_hash、checkpoint_schema_version、created_at。

**输出：** - 向后兼容扩展 `src/risk_agent/checkpoint/` -
`src/risk_agent/collection/decision_checkpoint.py` -
`tests/test_model_backed_checkpoint.py` -
`docs/phase2/checkpoint_protocol.md` - 日报

**Gate：** 至少 5 个真实 Qwen 任务保存 Checkpoint；5/5 Restore hash
一致；对话、Memory、工具状态、文件和 SQLite
可恢复；不含未来结果或隐藏答案；旧 Day 5/6/9 格式仍可读取。

------------------------------------------------------------------------

## P2-D3：实现模型驱动 Branch Runner

**目标：** 将本地 Reference Branch Runner 扩展为真实 Qwen 分支执行器。

**步骤：**

1.  保留 Reference 模式，新增显式 `model_backed` 模式；summary 必须记录
    controller 类型。
2.  每个 Checkpoint 的每个动作从独立目录 Restore。
3.  同一 Checkpoint 的各动作初始 combined state hash 必须完全相同。
4.  每个分支使用独立文件目录、SQLite、RuntimeState 和 Mock API 状态。
5.  实现 Continue / Retry / Replan 固定语义。
6.  所有分支使用相同模型、系统 Prompt、工具版本和 generation config。
7.  同一 Checkpoint 的所有分支固定分配到同一 Worker；不同 Checkpoint
    可在两个 Worker 间均衡。
8.  记录 Worker、Endpoint、模型调用次数、prompt/completion tokens、model
    latency、tool latency、总 wall-clock latency。
9.  工具副作用发生后模型服务中断，不自动重放整个任务。
10. 仅当 infra failure
    发生在任何工具副作用之前，并确认无副作用，才允许按同一 branch ID
    安全续跑。
11. 不适用动作记录原因，不执行，不训练 outcome/cost 标签。

**输出：** - `src/risk_agent/intervention/model_backed_runner.py` -
`src/risk_agent/branching/model_backed_branch.py` -
`tests/test_model_backed_branching.py` -
`configs/phase2_branching.yaml` -
`docs/phase2/model_backed_branch_protocol.md` - 日报

**Gate：** 至少 5 个真实 Checkpoint 完成动作适用性判定；所有执行分支
Restore hash 匹配；分支无状态串扰；Retry 安全规则通过；Continue
无额外干预 Prompt；Replan 不使用未来信息；Token/Latency 缺失为
null；Reference 与 model-backed 产物明确区分。

------------------------------------------------------------------------

## P2-D4：小规模真实模型 Smoke 与安全验收

**目标：** 在 8--12 个 Train 任务上验证完整链路。Smoke 只使用 Train
或专用开发模板，不用 Test 调试。

**步骤：**

1.  运行单元和集成测试。
2.  检查远程 Worker 状态，动态选择最多两张空闲 RTX 4090。
3.  用已有脚本启动服务和 SSH 隧道；严格 TLS 校验。
4.  先运行短请求和 Tool Call smoke。
5.  运行 8--12 个 Train 任务，覆盖四个领域和至少四类故障。
6.  至少生成 5 个真实决策 Checkpoint。
7.  对每个 Checkpoint 执行所有安全适用动作。
8.  验证 Restore、隔离、Verifier、Token 和成本日志。
9.  结束后停止本次 Worker/隧道并确认 GPU 释放。
10. Smoke 单独标记 `formal_experiment=false`。

**输出：**
`runs/phase2_smoke/<run_id>/`、`smoke_summary.json`、`checkpoint_restore_audit.json`、`branch_isolation_audit.json`、日报。

**Gate：** 8--12 个任务无编排错误；至少 5 个 Checkpoint 可恢复；分支初始
hash 一致；Verifier 有效；真实 Token/Latency
可记录；资源释放。失败则修复并重做 smoke，不得进入正式采集。

------------------------------------------------------------------------

## P2-D5：Train split 正式采集

**目标：** 采集真实模型 Train 轨迹和同状态动作结果。

**范围：**

-   Train 模板 48 个；每模板 2 个实例，计划 96 个任务。
-   目标至少 72 个有效 Train Checkpoint，目标 96。
-   每个任务优先捕获一个预注册决策时刻；多个时刻都可记录，但统计按
    task/template 聚类。
-   每个 Checkpoint 执行 Continue 和所有安全适用 Retry/Replan。
-   至少 20% Checkpoint-action 组合独立重复。

**步骤：**

1.  Preflight：catalog
    hash、split、模型、Worker、TLS、工具版本、Verifier、磁盘空间。
2.  按固定顺序执行任务，fault schedule 写入 manifest。
3.  原始 attempts append-only。
4.  infra failure 与 task failure 分开。
5.  pre-tool infra failure 时暂停批次；核验服务后再安全续跑。
6.  工具副作用发生后，不得自动重跑整个任务。
7.  在决策时刻保存真实模型 Checkpoint。
8.  同 Checkpoint 执行所有适用动作分支。
9.  Deterministic Verifier 判定结果，记录真实模型和工具成本。
10. 采集结束停止本次 Worker/隧道并检查 GPU 释放。

**输出：** `runs/phase2_train/<run_id>/`，至少包含
`task_attempts.jsonl`、`decision_checkpoints.jsonl`、`branch_outcomes.jsonl`、`tool_trajectories.jsonl`、`model_usage.jsonl`、`checkpoint_manifest.json`、`collection_summary.json`
和日报。

**Gate：** 至少 72 个有效 Train Checkpoint；Continue
全覆盖；Retry/Replan 适用性完整；Retry/Replan 适用 Train 结果尽量各 30
条以上；每个非 Continue 动作正负类至少各 10
条才允许训练状态条件模型；真实 Token/Latency 记录率 ≥95%；Restore hash
匹配率 100%；无跨 split/分支污染。

------------------------------------------------------------------------

## P2-D6：Validation split 正式采集

**目标：** 采集独立 Validation
Checkpoint，用于模型选择、概率校准、阈值和成本权重选择。

**范围：** 16 个 Validation 模板，每模板 2 个实例，计划 32 个任务；至少
24 个有效 Checkpoint。

**步骤：**

1.  Preflight 核验 template IDs 全属于 Validation。
2.  不因 Train 结果不理想而临时修改 Validation 模板。
3.  使用相同模型、工具、Prompt、fault schedule 规则、Checkpoint schema
    和动作语义。
4.  采集真实轨迹、Checkpoint 和所有安全适用分支。
5.  完成 Verifier、成本、Restore 和完整性检查。
6.  不按 Validation 结果修改任务模板、故障分布或动作语义。
7.  若发现实现 Bug，创建新 protocol/config
    version，记录受影响数据，不覆盖旧 run。

**输出：**
`runs/phase2_validation/<run_id>/`、`validation_summary.json`、manifest、日报。

**Gate：** 至少 24 个有效 Checkpoint；模板与 Train 无交集；分支初始 hash
一致；适用性、Verifier、成本齐全；不把反复调过的 Validation
称为独立验证。

------------------------------------------------------------------------

## P2-D7：Test split 正式采集与锁定

**目标：** 为最终独立评估准备 Test Checkpoint
和所有安全适用动作的真实结果。

**步骤：**

1.  先冻结 Protocol、模板、模型、Prompt、工具、Verifier、特征
    allowlist、指标和训练配置。
2.  Test 模板必须全部属于 Test split。
3.  使用与 Train/Validation 一致的执行协议。
4.  每个 Test Checkpoint 执行 Continue 和所有安全适用动作。
5.  记录分支结果，但不查看模型策略在 Test 上的聚合表现。
6.  只检查 schema、完整性、hash、split 隔离和敏感信息。
7.  Test 标签写入独立目录；训练脚本默认拒绝加载 Test。
8.  生成 Test lock manifest 和 SHA-256。

**输出：**
`runs/phase2_test_locked/<run_id>/`、`test_manifest.json`、`test_lock.json`、`test_integrity_report.json`、日报。

**Gate：** 至少 24 个有效 Test Checkpoint；与 Train/Validation
模板完全不重叠；适用动作结果完整；Test
标签未参与训练和调参；manifest/hash 固定。若协议 Bug 影响
Test，废弃该版本并升版本重新采集，不得边看 Test 结果边修补。

------------------------------------------------------------------------

## P2-D8：数据质量审计与冻结

**目标：** 判断真实数据是否足以支持 Phase
3；只扩充不足部分，不盲目扩大全部数据。

必须审计：

1.  任务执行数、infra_failure、task_failure、success；
2.  Checkpoint 数量和 split 分布；
3.  Domain × Fault × Split 覆盖矩阵；
4.  Continue / Retry / Replan 适用性；
5.  每个动作 success/failure 数；
6.  Checkpoint hash、Restore hash、branch ID 唯一性；
7.  文件、SQLite、RuntimeState、工具状态隔离；
8.  Prompt/Completion Token、模型延迟、工具延迟和总延迟缺失率；
9.  Worker 分布；
10. 原始轨迹和 manifest 的 SHA-256；
11. Test 训练隔离守卫；
12. 敏感信息扫描。

数据 Gate：

-   最低 120 个有效真实 Checkpoint，目标 160。
-   最低 Train/Validation/Test = 72/24/24。
-   Retry、Replan 的 Train 适用样本尽量各不少于 30。
-   每个非 Continue 动作 Train 正负类各至少
    10；否则动作模型仍属数据不足。
-   不适用动作不能算失败。
-   补数据优先扩充 Train；修改 Validation/Test 必须升版本并重新冻结。

**输出：**
`docs/phase2/data_quality_report.md`、`runs/phase2_frozen/data_manifest.json`、`feature_allowlist.json`、`split_manifest.json`、`source_hashes.json`、日报。

**Gate：** Checkpoint Restore、真实模型分支、动作覆盖、来源和 split
隔离全部通过，Phase 2 才完成。

# 7. 阶段三：风险/效用模型、策略对比和独立评估

## P3-D1：冻结数据集与最终训练样本

**目标：** 将 Phase 2
原始轨迹转换为模型样本，保证特征和标签严格按决策时刻对齐。

**步骤：**

1.  读取 Phase 2 冻结 manifest 并校验 SHA-256。
2.  每个 Checkpoint 建立一条风险样本：`x`
    只来自决策时刻，`y_risk = 1 - success_continue`。
3.  每个适用动作建立 outcome/cost 样本；`y_outcome` 来自确定性
    Verifier。
4.  不适用动作保留审计记录，但不进入 outcome/cost 训练。
5.  每个特征重新执行 allowlist 检查。
6.  检查同一 task 多个 Checkpoint 的相关性，后续按 template 聚类。
7.  检查 Train/Validation/Test 模板互斥。
8.  生成训练数据 hash；Test 不得进入训练文件。

**输出：**
`runs/phase3_dataset/<run_id>/risk_dataset.*`、`utility_dataset.jsonl`、`feature_provenance.json`、`data_leakage_audit.json`、`dataset_summary.json`、日报。

**Gate：** 特征可追溯且决策时刻可观测；标签可追溯到分支；不适用动作无
outcome/cost 标签；Test 不进入训练数据。

------------------------------------------------------------------------

## P3-D2：重新训练与校准风险模型

**目标：** 在真实模型轨迹上学习 Continue 的条件失败风险。

**模型基线：**

1.  `constant_prevalence`：训练集失败率常数预测；
2.  `logistic_regression`：可解释基线；
3.  `lightgbm`：只有在样本量和类别数足够时使用；
4.  `history_only`：仅 Agent 历史特征；
5.  `deployment_allowlist`：仅允许部署的可观测特征。

**步骤：**

1.  Train 内按 `template_id` 分组交叉验证。
2.  所有模型使用相同 folds。
3.  报告 AUROC、AUPRC、Brier、Log Loss、Balanced Accuracy。
4.  报告每折样本数和正负类数量；单类别折的指标记为 N/A。
5.  报告校准曲线、校准截距/斜率；样本不足时标注不稳定。
6.  通过 Validation 选择模型、校准方法和风险阈值。
7.  不因 LightGBM 更复杂就默认选择它。
8.  去除关键响应特征后重新检查性能变化。
9.  保存训练配置、fold manifest、模型 hash、特征 schema 和校准器。

**输出：** `models/phase3/risk/<run_id>/`、OOF
predictions、metrics、calibration、feature ablation、日报。

**Gate：** Train OOF 和 Validation 均有指标；不能用 OOF 满分冒充
Test；捷径和校准问题有明确说明。

------------------------------------------------------------------------

## P3-D3：重新训练动作结果与成本模型

**目标：** 学习不同状态下 Continue / Retry / Replan
的条件成功概率和成本。

### 结果模型

分别训练：

\[ p_a(x)=P(Y_a=1`\mid `{=tex}x,a,`\mathrm{applicable}`{=tex}) \]

至少比较动作成功率常数模型、Logistic Regression；样本量足够时再比较
LightGBM。

若某动作只有一个结果类别：

-   使用显式常数/先验模型并标记 `single_class_fallback=true`；
-   不得声称学到了状态条件效用；
-   继续补充真实数据，直到类别 Gate 通过。

### 成本模型

分别预测 Prompt Tokens、Completion Tokens、Tool Calls、Model
Latency、Tool Latency、Total Wall-clock Latency。

至少比较均值/中位数常数模型和
Ridge/ElasticNet；数据量足够时再比较树模型。成本预测必须非负，真实缺失值不得填
0。

### 指标

-   Outcome：Brier、Log Loss、MAE。
-   Cost：各成本分量 MAE、MedAE、P90 误差。
-   Utility：按冻结效用定义计算 Utility MAE。
-   分动作报告 coverage 和正负类数。
-   Validation 用于模型选择和校准，不访问 Test。

**输出：** `models/phase3/utility/<run_id>/`、outcome/cost OOF
predictions、metrics、日报。

**Gate：** Retry/Replan
不得只有单类别却宣称完成条件学习；成本基于真实模型
Token/Latency；不适用动作无 outcome/cost 标签；模型可加载并通过
schema/hash 检查。

------------------------------------------------------------------------

## P3-D4：策略基线与消融重新实现

**目标：** 保证每个策略在数学定义、代码路径和动作选择上真正不同。

### 固定策略定义

1.  **Continue Only**：始终 Continue。
2.  **Always Retry**：Retry 安全且适用时 Retry，否则 Continue 或 Stop。
3.  **Always Replan**：Replan 安全且适用时 Replan，否则 Continue 或
    Stop。
4.  **Rule-based**：只根据决策时刻可见的工具反馈/状态执行固定规则，不使用模型预测。
5.  **Risk-only**：风险达到阈值时干预，按预定义固定动作优先级选择安全动作；不读取
    outcome/cost 模型。
6.  **Utility-only**：不使用风险门控，在安全适用动作中按预测成功概率选择。
7.  **Risk + Outcome**：风险门控 + 动作成功概率；不使用成本预测排序。
8.  **Full**：风险门控、动作 outcome、成本预测和预算约束全部启用。
9.  **Oracle（仅上界）**：使用同一 Test Checkpoint
    的已观测分支结果和成本，选择事后最佳安全动作；只作经验上界，不参与训练或调参。

Full 的净效用：

\[ `\Delta `{=tex}U(a`\mid `{=tex}x)=
`\hat `{=tex}p_a(x)-`\hat `{=tex}p\_{`\mathrm{continue}`{=tex}}(x)
-`\lambda`{=tex}\^`\top`{=tex}`\Delta`{=tex}`\hat `{=tex}C_a(x) \]

其中
(`\Delta`{=tex}`\hat `{=tex}C_a=`\hat `{=tex}C_a-`\hat `{=tex}C\_{`\mathrm{continue}`{=tex}})。成本分量的归一化尺度和权重只能在
Train/Validation 上确定。只有净效用增益超过 Validation
确定的阈值，且动作安全、适用、预算允许时才干预。

### 消融定义

-   `Full w/o Risk`：移除风险门控，其他模块不变。
-   `Full w/o Outcome Utility`：移除动作结果预测差异，改用固定动作先验/规则。
-   `Full w/o Cost`：移除成本预测和成本惩罚，但保留统一硬安全预算。
-   每个消融必须通过单元测试证明对应模块确实未被调用。
-   每条策略输出动作评分、排除原因、最终选择和模块调用记录。

**Gate：** 策略定义、配置、代码路径一一对应；Full 与 Risk + Outcome
不得复用同一逻辑仅改名称；所有策略使用相同安全层；同一 Test Checkpoint
配对比较。

**输出：**
`src/risk_agent/evaluation/policies_v2.py`、`configs/phase3_policies.yaml`、`tests/test_phase3_policy_ablation.py`、策略定义文档和日报。

------------------------------------------------------------------------

## P3-D5：最终 Test 评估与统计分析

**目标：** 在冻结 Test 上进行一次独立评估。

**步骤：**

1.  检查模型、策略、阈值、成本权重和 Test manifest hash。
2.  启用 Test 访问守卫。
3.  加载冻结模型和策略，不重新训练或改参数。
4.  对每个 Test Checkpoint 根据决策特征生成策略动作。
5.  在该 Checkpoint
    已真实执行并验证的安全适用分支中读取被选动作结果和成本。
6.  同 Checkpoint 比较所有策略。
7.  按 template 聚类做 Bootstrap 95% CI，建议至少 2,000 次重采样。
8.  如使用显著性检验，使用配对检验并报告效应量；小样本不作强显著性表述。
9.  分层结果均报告样本数和区间；样本不足的 Domain/Fault 只做描述性分析。
10. Test 结束后冻结结果，不得根据 Test 改阈值再称为独立 Test。

### 必须报告

**风险层：** AUROC、AUPRC、Brier、Log Loss、Calibration Curve，以及
Domain/Fault/历史工具次数分层。

**结果与成本层：** Outcome Brier/MAE、Cost MAE/MedAE、Token、Model
Latency、Tool Latency、Tool Calls、Retry/Replan coverage 和正负类数。

**策略层：** - Task Success Rate； - Recovery Rate； - Intervention
Rate； - Unnecessary Intervention Rate； - Cost per Successful
Recovery； - Tokens、Latency 的 mean/median/P90； - Tool Calls； -
Policy Value / Net Utility； - Policy Regret； - 与 Continue
Only、Rule-based、Risk-only 等基线的配对差值和 95% CI。

### 指标定义

-   `Task Success Rate`：Verifier 成功数 / 策略任务数。
-   `Recovery Rate`：源 Continue 失败 Checkpoint 中，策略所选非 Continue
    动作成功数 / 源 Continue 失败 Checkpoint 数；分母为 0 记 N/A。
-   `Intervention Rate`：Retry 或 Replan 数 / Checkpoint 总数。
-   `Unnecessary Intervention Rate`：Continue 实际成功却选择干预的数量 /
    全部干预数量。
-   `Cost per Successful Recovery`：干预分支总成本 / 成功恢复数；分母为
    0 记 N/A。
-   `Policy Regret`：同一 Checkpoint 的 Oracle
    经验价值减策略所选动作经验价值。Oracle 只作上界。
-   所有平均值报告样本数、标准差或置信区间。

**输出：** `runs/phase3_test/<run_id>/`、预测/决策/结果
JSONL、`test_metrics.json`、`paired_policy_comparisons.json`、`bootstrap_confidence_intervals.json`、`final_test_summary.json`、最终报告和日报。

**Gate：** Test manifest 与冻结版本一致；模型和阈值在 Test
前冻结；策略配对；不适用动作不计失败；结论有样本数和区间；结果不理想也不得事后调参重跑。

------------------------------------------------------------------------

## P3-D6：复现、最终报告与简历证据

**目标：** 将实验转化为可复现、可展示、可经受面试追问的项目成果。

**步骤：**

1.  整理环境检查、Worker 启停、采集、训练、评估和资源释放命令。
2.  生成数据版本、模型版本、配置 hash 和 run manifest。
3.  运行全量测试、依赖检查、编译检查。
4.  使用独立脚本从冻结原始数据重算关键统计。
5.  生成架构图、数据流图和干预流程图。
6.  生成最终实验报告和复现文档。
7.  生成 `resume_evidence.md`，只列出原始产物支持的数字。
8.  区分工程成果、研究发现、负面结果和限制。
9.  更新 README，不覆盖旧日报和旧实验报告。
10. 准备面试 Demo：真实任务 → 中间 Checkpoint → 三种动作分支 → Verifier
    → 成本 → 策略选择。

**输出：** - `docs/phase3/final_experiment_report.md` -
`docs/phase3/reproducibility_v2.md` - `docs/phase3/resume_evidence.md` -
`docs/phase3/demo_script.md` -
`docs/phase3/daily_reports/p3_d6_report.md` - 更新后的 README

**最终 Gate：** 代码/配置/数据/模型版本可追溯；指标可重算；真实模型与
Reference 分层；Test 独立性有
manifest/hash；结论不超出证据；简历数字能追溯到具体产物；全量测试通过；远程资源释放。

# 8. 建议的数据结构

Codex 实施前先审计现有
schema，优先向后兼容扩展；以下是必须表达的信息，不要求机械照抄字段名。

## 8.1 Decision Checkpoint

``` json
{
  "checkpoint_id": "unique_id",
  "source_task_run_id": "task_run_id",
  "template_id": "template_id",
  "task_id": "task_id",
  "instance_id": "instance_id",
  "split": "train",
  "catalog_hash": "sha256",
  "controller_type": "qwen3_model",
  "model_id": "Qwen3-8B",
  "worker_id": "worker_0",
  "step_index": 3,
  "decision_trigger": "tool_error_observed",
  "messages_ref": "relative_artifact_path",
  "runtime_state_ref": "relative_artifact_path",
  "environment_manifest_ref": "relative_artifact_path",
  "pending_operation": {},
  "latest_tool_response": {},
  "observable_state": {},
  "internal_fault_metadata": {},
  "combined_state_hash": "sha256",
  "checkpoint_schema_version": "2.0"
}
```

`internal_fault_metadata` 只用于实验分层，不得自动进入部署型风险特征。

## 8.2 Branch Outcome

``` json
{
  "branch_id": "unique_branch_id",
  "checkpoint_id": "checkpoint_id",
  "template_id": "template_id",
  "split": "train",
  "action": "RETRY",
  "applicable": true,
  "not_applicable_reason": null,
  "controller_type": "qwen3_model",
  "execution_mode": "model_backed_branch",
  "source_state_hash": "sha256",
  "restored_state_hash": "sha256",
  "final_state_hash": "sha256",
  "verifier_passed": true,
  "outcome": "success",
  "prompt_tokens": 1234,
  "completion_tokens": 128,
  "tool_calls": 1,
  "model_latency_ms": 800,
  "tool_latency_ms": 12,
  "wall_clock_latency_ms": 950,
  "failure_category": null,
  "worker_id": "worker_0",
  "attempt_id": "unique_attempt"
}
```

这是 schema 示例，不代表真实数据。真实缺失的 Token/Latency 必须写
`null`，不能用示例值或 0 填充。

## 8.3 Collection Manifest

每个 run 必须保存：

-   run ID、开始/结束时间；
-   catalog hash、template split hash；
-   代码版本或 Git 状态摘要；
-   模型 ID、vLLM 版本、Worker 映射；
-   Prompt / Tool / Verifier 版本；
-   generation config；
-   fault schedule 和随机种子；
-   Checkpoint schema 版本；
-   输入与产物 SHA-256；
-   失败 attempt 数；
-   资源释放状态；
-   `formal_experiment`、`passed` 和逐项 Gate。

------------------------------------------------------------------------

# 9. Codex 实现与代码质量要求

1.  优先复用现有模块，不重写整个 Agent、Checkpoint、Branch、Verifier
    或远程管理系统。
2.  新功能通过新模块或兼容扩展实现，旧 Day 1--14 脚本必须仍可运行。
3.  新配置必须有 schema validation；未知字段应报错或明确忽略并记录。
4.  JSONL append-only；续跑通过 run ID 和 attempt ID
    管理，不覆盖旧记录。
5.  正式采集先 smoke 和 preflight。
6.  模型服务错误、工具错误、Agent 推理失败、Verifier failure 分开分类。
7.  发生工具副作用后禁止无条件自动重跑。
8.  Checkpoint Restore 前后校验 checksum 和 combined state hash。
9.  所有 branch 使用唯一工作目录和 SQLite 文件。
10. 所有策略统一经过安全/适用性 Evaluator，策略不得绕过安全层。
11. 训练和评估脚本检查 split，训练脚本默认拒绝加载 Test。
12. 脚本应支持 `--smoke`、`--run-id` 或等效可追溯参数。
13. 命令和配置写入日报，密钥不写入。
14. 不确定的 API/依赖行为先用最小测试验证。
15. 关键统计使用独立审计脚本复算，不只相信训练脚本自己生成的 summary。

------------------------------------------------------------------------

# 10. 每日 Codex 启动 Prompt 模板

每次新开 Codex 会话，将下面内容粘贴进去，把 `P1-D1` 替换为当天任务编号。

``` text
请先完整阅读：
1. AGENTS.md
2. PROJECT_GUIDE.md
3. README.md
4. NEXT_STAGE_EXPERIMENT_GUIDE.md
5. 当前阶段已有的 protocol、audit 和 daily report

本次只执行：P1-D1（替换为我指定的任务编号）。

项目根目录：
/home/three/projects/risk-aware-smolagents

执行要求：
- 工作区必须是本地 WSL，不要迁移到远程服务器。
- 先检查 pwd、git status、现有文件和相关 run。
- 先给出本日计划、涉及文件、风险和验收标准，再开始修改。
- 严格遵守指南中的本日步骤和 Gate。
- 不得提前执行后续日任务。
- 不得覆盖、删除或重建 Day 1–14 的任何历史产物。
- 不得修改外部 Recovery-Bench 仓库。
- 不得启动远程 GPU 或批量 Qwen 采集，除非本日任务明确要求且我已批准。
- 所有正式产物使用新 run ID 和新目录。
- 先 smoke，再正式运行。
- 运行专项测试和全量回归。
- 生成 docs/phase2/daily_reports/<task_id>_report.md。
- 日报列出实际命令、修改文件、产物路径、测试结果、数据 Gate 和未解决问题。
- 不得把工程 PASS 写成研究结论成立。
- 若 Gate 不通过，停止并报告原因，不得跳过。

完成本日后：
1. 给出 PASS / CONDITIONAL PASS / BLOCKED。
2. 列出关键数字和原始产物路径。
3. 明确下一日是否具备启动条件。
4. 停止，不要自动开始下一日。
```

------------------------------------------------------------------------

# 11. 最终研究结论判定规则

Codex 最终必须按证据给结论，不能只写"模型效果很好"。

### A. 可以支持风险预测有效

需要：

-   风险模型在独立 Test 上优于常数基线；
-   Brier / Log Loss 和校准结果有改善；
-   去掉故障注入 metadata 后仍有合理预测能力；
-   不同模板、领域和故障类型上的表现没有完全崩溃；
-   报告样本量和置信区间。

### B. 可以支持动作效用具有条件差异

需要：

-   Retry、Replan 均有足够适用样本；
-   每个动作有成功和失败结果；
-   同一 Checkpoint 的分支从相同状态恢复；
-   Outcome 模型在独立 Test 上有预测能力；
-   不同状态下动作排序确实发生变化，而不是固定选择某一个动作。

### C. 可以支持风险感知策略有效

需要：

-   Full 在独立 Test 上与 Continue
    Only、Rule-based、Risk-only、Utility-only 等进行同 Checkpoint
    配对比较；
-   成功率、恢复率或成本有明确且稳定的改进；
-   不必要干预率和成本没有不可接受的恶化；
-   Bootstrap 置信区间支持结论；
-   策略消融确实改变决策行为，能解释组件贡献。

### D. 只能支持工程系统实现

如果模型或策略在独立 Test 上没有稳定优势，仍可如实展示：

-   真实 Agent 轨迹采集；
-   中间状态 Checkpoint；
-   同状态独立分支；
-   故障恢复与安全约束；
-   真实模型成本记录；
-   可复现评测系统。

这仍是有价值的 Agent 工程项目，但不能包装成已验证的风险感知干预算法。

------------------------------------------------------------------------

# 12. 最终交付清单

``` text
docs/
  phase2/
    audit_inventory.md
    risk_feature_audit.md
    utility_policy_audit.md
    protocol_v2.md
    checkpoint_protocol.md
    model_backed_branch_protocol.md
    data_quality_report.md
    daily_reports/
  phase3/
    policy_definitions.md
    final_experiment_report.md
    reproducibility_v2.md
    resume_evidence.md
    demo_script.md
    daily_reports/

configs/
  task_templates_v2.yaml
  phase2_collection.yaml
  phase2_branching.yaml
  phase3_policies.yaml

runs/
  phase2_catalog/
  phase2_smoke/
  phase2_train/
  phase2_validation/
  phase2_test_locked/
  phase2_frozen/
  phase3_dataset/
  phase3_test/

models/
  phase3/
    risk/
    utility/
```

实际目录可以按现有项目规范调整，但必须保持职责边界和可追溯关系。

------------------------------------------------------------------------

## 13. 给项目负责人的执行优先级

顺序不要改变：

1.  审计 Day 10--13，确认捷径特征、单类别动作和策略重复问题。
2.  实现真实 Qwen 中间状态 Checkpoint 和模型驱动 Branch Runner。
3.  扩充真实模型 Train / Validation / Test 数据。
4.  重训风险、动作结果和成本模型。
5.  在独立 Test 上完成策略对比。

**当前最重要的技术里程碑不是再训练一个更复杂的分类器，而是让真实
Qwen3-8B 在同一个可验证的中间状态上，分别执行 Continue、Retry 和
Replan，并完整记录结果与成本。**

只有这一步真正完成，后续风险预测、效用预测和自适应干预策略才有可靠的数据基础。
