# 工作线 B：真实 Agent 风险预测数据重建与采集指导书

> 版本：1.0  
> 编制日期：2026-11-19  
> 适用项目：MiniToolBench-R / `risk-aware-smolagents`  
> 主工作区：本地 WSL2 Ubuntu 24.04  
> 项目路径：`/home/three/projects/risk-aware-smolagents`  
> 主要 Agent：smolagents + Qwen3-8B（vLLM OpenAI-compatible HTTPS API）  
> 文档用途：新 Codex 对话窗口的长期工作指导文件  
> 本工作线：**重新建立真实 Qwen Agent 风险预测数据**，不负责 P1-D3 历史效用审计。

---

## 0. 给新 Codex 会话的最高优先级指令

本文件是工作线 B 的专用执行指南。新会话必须先阅读本文件和仓库现有规范，再审计当前实现。不要直接开始改代码或启动 GPU。

**本工作线的首要目标不是扩大样本量，而是建立可信的数据生成机制：**

1. Qwen3-8B 必须真实参与任务执行。
2. Checkpoint 必须来自真实 Agent 执行中的决策时刻。
3. Checkpoint 必须包含足以恢复 Agent 下一步推理的上下文，而不只是环境状态。
4. 风险特征必须在预测时刻真实可观测。
5. 风险标签必须来自同一 Checkpoint 的真实 CONTINUE 后续执行和确定性 Verifier。
6. 模型服务故障、环境故障、Agent 失败和任务验证失败必须分开记录。
7. 原始轨迹和历史 Day 1–14 产物只追加、不覆盖。
8. 工程 Gate 通过不等于风险预测研究假设成立。

**新 Codex 会话的第一轮只做现状审计和实施方案，不进行正式代码修改、GPU 启动或批量采集。** 完成现状审计后，先向用户汇报差距、方案、预计修改文件、资源需求和验收 Gate，等待确认。

---

## 1. 研究目标与范围

### 1.1 核心研究问题

> Agent 在执行过程中，能否仅根据决策时刻已经获得的历史行为、工具响应和运行状态，预测其按照原计划继续执行时的失败风险？

对决策时刻 \(t\)：

\[
r_t = P(Y_{\mathrm{continue}}=0 \mid X_{\leq t})
\]

其中：

- \(X_{\leq t}\)：截至决策时刻已经可观测的 Agent 历史和运行状态。
- \(Y_{\mathrm{continue}}=1\)：从该 Checkpoint 不添加干预，继续正常执行后，任务最终通过确定性 Verifier。
- \(Y_{\mathrm{continue}}=0\)：从该 Checkpoint 正常继续执行后，任务未通过确定性 Verifier。
- \(r_t\)：继续执行的失败风险。

风险预测必须是 **pre-decision prediction**：特征在分支执行和最终验证之前冻结，不能把未来分支结果、Verifier 结果或内部故障注入标签放进输入。

### 1.2 本工作线的交付边界

**第一优先级：风险数据闭环。**

- 真实 Qwen3-8B 任务执行。
- 真实工具调用和 Observation 记录。
- 真实决策时刻 Checkpoint 捕获。
- Agent 上下文与任务环境可恢复。
- 从同一 Checkpoint 执行 CONTINUE。
- 确定性 Verifier 生成风险标签。
- 决策时刻特征提取和可观测性审计。
- Train / Validation / Test 分组隔离。
- 真实风险数据质量审计与冻结。

**第二优先级：干预效用扩展。**

如果后续继续研究 RETRY / REPLAN 的条件效用，则在 Checkpoint 和 CONTINUE 数据闭环通过后，再从同一 Checkpoint 执行安全适用的 RETRY / REPLAN。该部分需要单独验证动作适用性、分支隔离和动作成本。

不要为了研究风险预测而提前重写整个策略系统。也不要把风险数据采集与 P1-D3 历史效用审计混在同一个 Codex 任务中。

### 1.3 本工作线不做什么

- 不微调 Qwen3-8B。
- 不引入强化学习。
- 不引入多 Agent。
- 不进行全量 Terminal-Bench 迁移。
- 不把 Recovery-Bench 普通历史日志直接当作可恢复 Checkpoint。
- 不把 `deterministic_local_reference` 结果当作真实 Qwen 预测数据。
- 不把 Day 10 的 20 条样本继续扩充成主训练集。
- 不在未冻结协议前启动大规模正式采集。

---

## 2. 当前项目基线：必须如实继承

以下事实来自当前项目的 `PROJECT_GUIDE.md`、`NEXT_STAGE_EXPERIMENT_GUIDE.md`、Day 1–14 日报、P1-D1/P1-D2 审计以及现有代码包。新 Codex 必须再次在实际仓库核验，不得仅凭本文件猜测文件状态。

### 2.1 环境和职责

- 本地工作区：`/home/three/projects/risk-aware-smolagents`。
- 本地系统：WSL2 Ubuntu 24.04。
- Python 环境：优先使用项目 `.venv/bin/python`。
- 远程模型：Qwen3-8B，通过 vLLM OpenAI-compatible HTTPS API 提供推理。
- 远程服务器有 8 张共享 RTX 4090；每次最多使用 2 张，启动前动态检查资源。
- 本地负责 Agent harness、任务环境、工具、Checkpoint、分支、Verifier、采集、训练和分析。
- 远程只负责模型推理服务。
- 外部 Recovery-Bench 仓库：`/home/three/projects/agent-intervention/recovery-bench`，保持只读，不得 reset、clean、checkout、format、commit 或修改。

不要重新下载模型、重新安装远端环境或擅自重启现有推理服务。先审计已有的 Worker 管理脚本、TLS、CA bundle、API Key 配置和 SSH 隧道管理方式。

### 2.2 Day 7–14 的历史事实

| 阶段 | 已有结果 | 对新数据工作的意义 |
|---|---|---|
| Day 7 | 4 个领域、40 个模板、200 个实例；模板级 Train/Validation/Test = 24/8/8 | 可复用任务环境和 Verifier，但 Reference Runner 成功不等于 Qwen 能完成任务 |
| Day 8 | 24 个 Train 模板各执行一个实例；19 success、4 infra_failure、1 task_failure；43 条 attempts | 有真实 Qwen 初始执行轨迹，但没有可直接用于同状态分支的逐步 Checkpoint |
| Day 9 | 20 个 Checkpoint、60 条候选动作；36 条 applicable | 分支由 `deterministic_local_reference` 执行，不是真实 Qwen 模型驱动分支 |
| Day 10 | 20 个 Train Checkpoint、9 failure / 11 success、26 个特征；原始 OOF AUROC/AUPRC=1.0 | 不能作为真实 Qwen 风险预测证据 |
| Day 11 | 20 Checkpoint、60 动作候选、36 applicable；Retry 6/6 成功、Replan 10/10 成功 | 非 Continue 动作缺少失败类，不能支持可靠的条件效用学习 |
| Day 12–13 | 20 个 Train Checkpoint；9 种策略 × 20 = 180 条记录 | 策略结果是本地参考执行结果，多个策略动作完全相同 |
| Day 14 | Recovery-Bench 只读 Adapter 演示；37 ATIF steps、100 tool calls、34 observations | 普通日志解析和 best-effort replay 不等于完整 Checkpoint / Restore / Branch |

### 2.3 P1-D2 已确认的风险数据问题

P1-D2 的工程审计已完成，主要发现：

- 20 个风险样本均来自 `deterministic_local_reference`。
- `failure_response_received` 与失败标签完全分离：9 个 failure 全为 1，11 个 success 全为 0。
- 原始 LightGBM 的 80/80 split importance 来自该字段。
- 删除单列后，模型转而使用其他 `fault_*` 注入元数据，仍然得到满分。
- 删除全部 `last_failure` 元数据后，Logistic AUROC=0.631，LightGBM AUROC=0.646。
- `history_only` 的 AUROC 约为 0.68。
- 严格 Agent-visible 五特征代理的 AUROC=0.591。
- 当前 5 个所谓 Agent-visible 特征仍来自 synthetic history，没有证明真实 Qwen 当时收到对应 Tool Observation。
- 每个验证折只有 4 个样本；没有独立 Validation/Test 风险 Checkpoint。

因此：

**Day 10 的满分结果只能作为特征捷径审计对象，不能继续作为正式风险模型能力证据。**

P1-D2 的 PASS 表示审计工作完成，不表示风险预测假设已经成立。新数据必须使用独立的新 run、新 schema 版本和新模型目录，不能覆盖 Day 10 文件。

### 2.4 当前代码中的关键实现边界

在现有代码包中已观察到以下实现形态，新 Codex 必须对当前仓库重新核验：

1. `src/risk_agent/collection/initial_trajectory.py` 使用 `agent.run(instruction, return_full_result=True)` 完成一次任务运行；之后才调用 `agent.memory.get_succinct_steps()`。这意味着现有采集路径主要在任务结束后提取 Agent steps，并未证明具备逐工具响应时的中间 Checkpoint hook。
2. `RuntimeState.agent_memory` 在现有初始轨迹路径中至少包含用户任务消息，但这不等于完整的 smolagents 对话消息、Assistant tool-call 消息和 Tool Observation。
3. `src/risk_agent/checkpoint/manager.py` 已有环境文件复制、SQLite Backup API、RuntimeState 序列化、checksum 和 restore hash 检查。
4. 现有 Checkpoint Manager 保存的是 TaskEnvironment + RuntimeState + task config；不能仅凭它已支持环境恢复，就声称完整 Agent 推理上下文也可恢复。
5. `src/risk_agent/branching/runner.py` 中的已有分支执行器属于本地参考执行路径；新代码必须显式区分 `reference` 与 `model_backed`。
6. `ExecutionMonitor` 可以记录工具调用、结果、异常、Token 和延迟，但需要核验这些数据与真实 Agent 收到的消息是否一致。
7. `agent_factory.py` 使用 OpenAI-compatible Qwen API 和 smolagents `ToolCallingAgent`。真实 Checkpoint hook 必须基于当前安装的 smolagents 版本和真实对象生命周期实现。

**关键技术风险：当前 Checkpoint Manager 能恢复工具环境，不代表能恢复 Qwen 下一步推理所需的完整 Agent messages。这个问题必须先解决，才能采集正式风险标签。**

---

## 3. 风险数据的正确生成机制

### 3.1 一个有效风险样本是什么

一个有效风险样本必须对应一个真实的决策时刻：

1. Qwen3-8B 已经接收任务并实际执行。
2. Agent 已经收到某次工具调用的 Observation 或错误反馈。
3. 当前 Observation 已经进入 Agent 的对话上下文。
4. Agent 尚未进行下一次模型推理或下一次工具调用。
5. 此时捕获完整 Checkpoint。
6. 从 Checkpoint 的独立恢复副本执行 CONTINUE。
7. Qwen 按原有 Prompt 和正常流程继续，不添加风险提示或人工修正指令。
8. 任务结束后由确定性 Verifier 判定成功或失败。
9. 以该结果生成该 Checkpoint 的 `Y_continue` 标签。

示意：

```text
Task
  |
  v
Qwen3-8B reasoning
  |
  v
Tool Call
  |
  v
Tool Execution
  |
  v
Tool Observation / Error Response
  |
  v
Observation 已进入 Agent messages
  |
  |  <-- 在下一次模型推理前捕获 Checkpoint
  v
Freeze X_t (仅决策时刻可见信息)
  |
  v
Restore exact Agent + Environment state
  |
  v
CONTINUE (无额外干预)
  |
  v
Qwen3-8B 后续执行
  |
  v
Deterministic Verifier
  |
  v
Y_continue = success / failure
```

风险模型训练输入是 `X_t`，标签是从该 Checkpoint 后续 CONTINUE 得到的 `Y_continue`。不能用完整任务最终日志倒推决策时刻特征。

### 3.2 Checkpoint 的精确定义

决策时刻必须统一为：

> Agent 已经收到当前工具反馈，且该反馈已经进入 Agent 可恢复上下文；但 Agent 尚未基于该反馈发起下一次模型推理或工具操作。

至少需要保存：

**A. Agent 上下文**

- 完整 messages / conversation history；
- Assistant 的 tool-call 请求；
- Tool Observation；
- System Prompt、Task Prompt；
- smolagents Memory 中实际参与下一轮推理的状态；
- 当前 step index、agent state、pending action；
- 如框架还存在其他会影响下一轮推理的状态，也必须保存。

**B. 任务与环境状态**

- 当前任务配置和输入；
- Workspace 文件；
- SQLite 数据库；
- Mock API 状态；
- 文件版本、事务状态、幂等键；
- 工具注册表和工具版本；
- 已发生的工具副作用；
- 当前环境和任务状态 hash。

**C. Runtime 与预算**

- Tool history；
- 当前 pending operation；
- 最新 Tool Observation；
- Token budget；
- Tool-call budget；
- Latency budget；
- 已消耗预算；
- Agent memory / runtime metadata。

**D. 实验元数据**

- `experiment_id`、`run_id`、`task_run_id`；
- `template_id`、`task_id`、`instance_id`、`seed`；
- `split`；
- `checkpoint_id`、`step_index`；
- `controller_type`；
- `execution_mode`；
- `model_id`、`model_version`；
- `worker_id`；
- `prompt_version`、`tool_version`、`verifier_version`；
- `catalog_hash`、`checkpoint_schema_version`。

**E. 内部审计信息（禁止默认进入风险特征）**

- 故障注入类型；
- 注入位置和阶段；
- 注入 seed；
- fault schedule；
- 隐藏预期答案；
- Verifier 内部状态。

内部信息可以保存在独立的审计字段中，但不得混入 Agent-visible feature vector。

### 3.3 Checkpoint 完整性 Gate

对每个 Checkpoint：

1. 从源状态生成 snapshot。
2. 在至少两个独立 branch workspace 中分别 Restore。
3. 分别计算规范化 Agent-state hash 和 Environment-state hash。
4. 恢复后的状态必须与原 Checkpoint 对应。
5. 不同分支必须使用独立文件目录、SQLite、RuntimeState 和可变服务状态。
6. 分支 A 的写入不得影响分支 B。
7. 对每个分支记录 source hash、restored hash、final hash。

只有 Environment hash 相同而 Agent messages 不同，不算完整恢复成功。

如果当前 smolagents 版本不支持直接恢复 Memory，先评估：
- 通过公开 API 保存和重建完整 messages；
- 通过受控 Agent subclass / 自定义执行循环，在明确生命周期节点捕获和恢复；
- 通过独立的 Agent State Adapter 将框架消息状态映射为可序列化 schema。

不要未经验证 monkey-patch 私有方法。若必须使用版本相关内部接口，必须固定依赖版本、写兼容测试，并记录升级风险。

---

## 4. 风险标签和失败来源定义

### 4.1 主标签

\[
Y_{\mathrm{continue}} =
\begin{cases}
1, & \text{CONTINUE 分支通过确定性 Verifier}\\
0, & \text{CONTINUE 分支未通过确定性 Verifier}\\
\mathrm{null}, & \text{无法有效观察到任务结果}
\end{cases}
\]

`null` 不是负类。无法观察到结果的样本必须保留原因，不能人为填成失败。

### 4.2 必须区分的结果类型

| 结果来源 | 例子 | 风险标签处理 |
|---|---|---|
| Agent 任务失败 | 错误工具参数、错误计划、重复调用、未完成任务 | 根据预注册 Verifier 和终止规则标记 |
| 工具响应失败 | API 503、Timeout、Invalid Argument | 如果真实响应已返回给 Agent，继续执行后由 Verifier 判定 |
| 环境故障 | SQLite 损坏、Mock API 服务异常、文件系统异常 | 标记环境/基础设施故障；不能直接算 Agent 失败 |
| 模型服务故障 | vLLM 不可用、TLS 失败、API 超时 | 记录 infra failure；若没有有效后续结果，风险标签为 null |
| 验证失败 | 最终输出不符合任务要求 | `Y_continue=0` |
| 人工中止 / 预算中止 | 操作员终止、预算耗尽 | 按预先定义的任务终止规则处理；不可事后选择性标注 |

尤其要避免把“Qwen API 请求失败”当作“Agent 在任务环境中执行失败”。这会混淆模型服务可用性与 Agent 行为风险。

### 4.3 终止规则必须预注册

每个任务模板必须定义：

- 成功条件；
- 最大 Agent steps；
- 最大 Tool Calls；
- 最大 Token；
- 最大 wall-clock time；
- 可接受的部分完成状态；
- 失败终止原因；
- infra failure 的排除规则；
- Verifier 输入和输出。

不能在看到结果后再调整终止规则。

---

## 5. 特征设计：严格遵守决策时刻可观测性

### 5.1 候选特征组

所有候选特征都必须记录来源、计算时刻、Agent 可见性和部署许可。

| 特征组 | 候选字段 | 使用要求 |
|---|---|---|
| 执行进度 | `step_index`、`tool_call_count`、`observed_history_count` | 仅使用当前时刻之前的记录 |
| 工具行为 | `unique_tool_count`、`repeated_action_count`、`repeat_ratio` | 从真实 Tool Call history 计算 |
| 工具响应 | `observed_error_count`、`latest_observed_error_type` | 必须来自 Agent 实际收到的 Observation |
| 进展信号 | `no_progress_count`、`progress_delta` | 进展定义必须预先固定 |
| 历史可靠性 | `historical_tool_success_rate` | 只能统计当前时刻之前已完成的调用 |
| 预算 | `token_budget_fraction`、`tool_budget_fraction` | 由运行时监控器在决策时生成 |
| 时间 | `recent_tool_latency`、`recent_model_latency` | 只能使用过去已经发生的调用 |
| 任务上下文 | domain、任务复杂度、可见目标信息 | 只保留 Agent 当时能够获得的上下文 |

### 5.2 明确禁止的风险模型输入

以下信息只能用于审计、分层或结果分析，不能进入正式风险特征：

- `fault_type`、`fault_id`；
- `fault_f1_timeout` 至 `fault_f6_partial_failure` 等注入标签；
- `injection_seed`、`injection_phase`、`fault_schedule`；
- `last_failure` 中仅控制器可见的内部元数据；
- 最终任务状态；
- 最终答案和隐藏 expected answer；
- Verifier 的内部结果、隐藏检查字段；
- 后续 Tool Calls 和未来 Tool Observations；
- CONTINUE / RETRY / REPLAN 的未来分支结果；
- 未来 Token、未来延迟和未来成本；
- 任何由最终标签反向构造的字段。

注意：如果 API 503 的错误响应确实被返回给 Agent，那么 Agent-visible 的错误响应文本或结构化状态可以成为候选输入；但注入器内部的 `fault_type=F2` 仍必须单独保存。两者不能共用同一个字段。

### 5.3 特征审计表

每个特征必须有以下元数据：

```json
{
  "feature_name": "observed_error_count",
  "definition": "截至当前决策时刻，Agent 已收到的错误 Observation 数量",
  "source": "agent_messages_and_tool_observations",
  "available_at_decision_time": true,
  "visible_to_agent": true,
  "computed_from_future_data": false,
  "allowed_for_deployment": true,
  "audit_note": "必须由真实 messages/observations 重算"
}
```

正式训练前生成 `feature_allowlist.json`。训练脚本只接受 allowlist 中的字段；禁止仅靠特征名称判断是否安全。

---

## 6. 任务模板、故障和数据划分

### 6.1 四个任务领域

继续沿用当前项目的四个领域：

1. Data Analysis；
2. File Retrieval；
3. API Workflow；
4. Calculation & Logic。

新模板必须有真实工具依赖链，建议 3–8 个相互依赖的工具步骤。不要把单步任务仅通过增加文本长度伪装成多步骤任务。

### 6.2 故障类型 F0–F6

| ID | 类型 | 真实环境表现 | 关键注意事项 |
|---|---|---|---|
| F0 | No Fault | 正常工具响应 | 必须有无故障对照 |
| F1 | Timeout | 工具请求超时 | 区分请求未提交、已提交但响应丢失 |
| F2 | API 503 | 服务临时不可用 | 必须向 Agent 返回真实错误 Observation |
| F3 | Invalid Argument | 参数校验失败 | 错误信息需符合真实工具接口 |
| F4 | Stale Observation | 版本冲突、过期状态 | Agent 需要通过可见反馈识别过期信息 |
| F5 | Repeated Action / No Progress | 重复工具调用、无进展 | 重复行为应来自 Agent 执行轨迹，不能直接写入隐藏标签代替 |
| F6 | Partial Failure | 操作已提交但响应异常 | 明确 committed / not_committed / unknown |

故障注入器只负责控制环境响应。它可以记录内部 fault metadata，但不得直接向风险模型提供该字段。

### 6.3 建议采用的 Protocol v2 规模

现有 `NEXT_STAGE_EXPERIMENT_GUIDE.md` 已提出一版 Protocol v2 计划，供本工作线评估和确认：

- 总计 80 个模板：原 40 个模板保持原 ID、任务定义和 split；新增 40 个多步骤模板。
- 每个领域新增 10 个模板。
- 新增模板按每领域 6 Train、2 Validation、2 Test 分配。
- 合并后模板级 Train / Validation / Test = 48 / 16 / 16。
- 每模板计划采集 2 个实例，计划总计 160 次真实 Qwen 任务执行。
- 目标有效 Checkpoint：160；最低 Gate：120。
- 最低 Train / Validation / Test 有效 Checkpoint = 72 / 24 / 24。
- 目标 Train / Validation / Test Checkpoint = 96 / 32 / 32。

**这些数字是现有指南中的预注册建议，不是已经完成或已经冻结的实验事实。** 新 Codex 必须先核对原 catalog、模板生成器、采集成本和当前协议，再向用户确认是否沿用。不能未经确认改变模板分配、实例数量或最低样本门槛。

### 6.4 Split 规则

必须按 `template_id` 分组：

- 同一模板的全部实例只能属于一个 split。
- 同一任务的全部 Checkpoint 只能属于该任务所属 split。
- 同一 Checkpoint 的 CONTINUE / RETRY / REPLAN 分支必须属于同一个 split。
- Train 用于拟合模型。
- Validation 用于模型选择、校准、阈值和成本权重选择。
- Test 只用于最终一次独立评估。

Test 数据采集后可以做 schema、完整性、hash、split 检查；不能根据 Test 标签调整特征、阈值、Prompt、任务模板或模型超参数。

---

## 7. 数据采集阶段设计

严格按阶段推进，一次只做一个阶段。每阶段结束生成日报和 Gate 结论。

### B0：现有实现与协议审计（新 Codex 第一项任务）

**只读审计，不改代码、不启动 GPU。**

必须检查：

- `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`、`NEXT_STAGE_EXPERIMENT_GUIDE.md`；
- 最近 Day 1–14 日报；
- P1-D1 / P1-D2 审计报告；
- 当前 `smolagents` 版本及依赖锁定；
- `agent_factory.py`、`initial_trajectory.py`；
- `ExecutionMonitor`、`RuntimeState`；
- `CheckpointManager`、Checkpoint schema；
- Reference Branch Runner；
- TaskEnvironment、Fault Injector、Verifier；
- vLLM Worker 管理脚本和本地配置。

B0 必须回答：

1. 当前 smolagents 的真实推理循环在哪里？
2. 当前 Agent Memory 的结构是什么？
3. Tool Observation 何时写入 Memory？
4. 能否在 Observation 已进入 Memory、下一轮模型调用尚未发生时触发 hook？
5. 当前 Checkpoint 能保存哪些状态，缺少哪些状态？
6. 当前状态能否真正恢复 Qwen 的下一轮推理？
7. 若不能，最小改动方案是什么？
8. 当前工具是否有外部副作用？哪些可安全快照？
9. 当前 fault injector 是否真正向 Agent 返回错误响应？
10. 现有 Verifier 是否足以作为各模板的结果标签？

**B0 输出：**

- `docs/phase2/real_agent_gap_audit.md`
- `docs/phase2/real_agent_checkpoint_design.md`
- `docs/phase2/real_agent_collection_plan.md`
- `docs/phase2/daily_reports/b0_audit_report.md`

B0 Gate：给出可验证的实现差距、最小技术方案、修改文件清单、风险、算力和时间估计。用户确认前不开始实现。

### B1：冻结风险数据 Protocol 和 Schema

在 B0 方案基础上，确认：

- 决策时刻定义；
- 风险标签定义；
- 失败 / infra failure / censored 的处理；
- 特征 allowlist；
- F0–F6 注入语义；
- 模板和 split；
- Checkpoint schema；
- 原始事件 schema；
- 数据目标和最低 Gate；
- 评测指标与停止条件。

**输出：**

- `docs/phase2/protocol_risk_v2.md`
- `docs/phase2/schema_risk_v2.md`
- `configs/phase2_risk_collection.yaml`
- `configs/task_templates_v2.yaml`（仅在模板变更确认后）
- `docs/phase2/feature_allowlist.json`

B1 Gate：用户明确确认 Protocol v2 后才能实现正式采集路径。

### B2：真实决策时刻 Checkpoint 捕获

目标：在真实 Qwen Agent 的执行过程中保存完整可恢复状态。

实施要求：

1. 固定当前 smolagents 版本。
2. 通过公开 API、受控 subclass、callback 或受控执行循环实现 hook。
3. 不得只在 `agent.run()` 完成后保存 `get_succinct_steps()`。
4. 捕获点必须在 Observation 进入上下文后、下一轮模型推理前。
5. 保存完整 Agent messages 和必要的框架状态。
6. 保存工具环境、RuntimeState、预算、pending operation 和元数据。
7. 保存每个文件、数据库和状态对象的 checksum。
8. 实现 Restore 后的规范化 hash 比较。
9. 测试多次 Restore 的确定性。
10. 测试恢复后的 Agent 能够真正继续发起下一轮 Qwen 请求。

**核心测试：**

- Agent messages restore exact-match；
- Tool Observation 保留；
- Assistant tool-call message 保留；
- SQLite restore exact-match；
- Workspace restore exact-match；
- RuntimeState restore exact-match；
- Budget restore exact-match；
- Combined hash match；
- Restore 后 Qwen 可以继续；
- Checkpoint 生成前后的原始轨迹连续。

如果无法恢复完整 Agent 上下文，B2 必须 BLOCKED，不允许退化成仅保存环境状态并继续正式采集。

### B3：CONTINUE 模型驱动执行与标签生成

风险数据第一版只要求 CONTINUE 分支闭环。

每个 Checkpoint：

1. 从原始 Checkpoint 创建独立 branch workspace。
2. Restore Agent messages + Runtime + Environment。
3. 校验恢复 hash。
4. 不注入任何额外干预指令。
5. 使用与原任务一致的模型、Prompt、Tool、Generation Config。
6. 固定记录 Worker 和模型版本。
7. 由 Qwen 继续正常执行。
8. 由确定性 Verifier 判定最终结果。
9. 记录完整 Token、Tool Calls、Model Latency、Tool Latency 和 Wall-clock Latency。
10. 记录终止原因和 infra failure。
11. 生成 `Y_continue`。

CONTINUE 必须是原策略下的自然继续执行，不得在 System Prompt 中添加“请谨慎”“请避免失败”等额外提示。

**B3 Gate：** 至少 5 个真实 Checkpoint 通过完整 Restore → Qwen Continue → Verifier 闭环；每个 Checkpoint 的模型来源和状态 hash 可追溯。

### B4：小规模 Smoke 与安全验收

建议范围：

- 8–12 个 Train 任务；
- 覆盖四个领域；
- 覆盖至少四类故障；
- 至少 5 个有效真实决策 Checkpoint；
- 所有样本必须真实调用 Qwen3-8B。

Smoke 必须单独标记：

```json
{
  "formal_experiment": false,
  "execution_mode": "model_backed_smoke",
  "controller_type": "qwen3_model"
}
```

检查：

- Qwen 请求确实发生；
- Tool Calls 确实发生；
- Agent messages 完整；
- Checkpoint 可恢复；
- Continue 可继续执行；
- Verifier 可判定；
- Token/Latency 记录有效；
- 环境和分支没有串扰；
- GPU / Worker / SSH tunnel 正确释放。

Smoke 失败必须修复后重新执行，不得把 Smoke 结果并入正式训练集。

### B5：Train 正式采集

现有 Protocol v2 建议：

- 48 个 Train 模板；
- 每模板 2 个实例；
- 计划 96 次任务执行；
- 最低 72 个有效 Train Checkpoint；
- 目标 96 个有效 Train Checkpoint。

每个任务优先采集一个预注册决策时刻。若采集多个 Checkpoint，必须保留 task/template 聚类信息，统计时不能把同一任务的多个 Checkpoint 当作完全独立样本。

原始数据 append-only。任务 attempt、Checkpoint、CONTINUE outcome、model usage 分开记录。

**Train Gate：**

- 有效真实 Checkpoint ≥72；
- Continue 结果有成功和失败两类；
- 任务、模板、split 可追溯；
- Restore hash match = 100%；
- 模型 Token / Latency 可观测率 ≥95%；
- 无跨 split 泄漏；
- 无跨分支状态污染；
- 失败来源分类完整。

若失败样本不足，不得通过修改标签或人为注入标签凑数。可以根据预注册方案扩充 Train 模板/实例，但必须保留新版本配置和采集记录。

### B6：Validation 正式采集

现有 Protocol v2 建议：

- 16 个 Validation 模板；
- 每模板 2 个实例；
- 计划 32 次任务执行；
- 最低 24 个有效 Validation Checkpoint；
- 目标 32 个。

Validation 用于：

- 模型选择；
- 概率校准；
- 风险阈值选择；
- 特征选择（仅限 Protocol 允许的候选）；
- 风险门控参数选择。

不得因 Validation 结果不理想而临时修改任务模板或故障分布，再把它称为独立验证集。

### B7：Test 正式采集与锁定

现有 Protocol v2 建议：

- 16 个 Test 模板；
- 每模板 2 个实例；
- 计划 32 次任务执行；
- 最低 24 个有效 Test Checkpoint；
- 目标 32 个。

Test 采集前必须冻结：

- Protocol；
- 模板；
- 模型版本；
- Prompt；
- Tool；
- Verifier；
- Feature allowlist；
- 训练配置；
- 主要指标。

Test 标签与训练文件隔离。生成 SHA-256 manifest 和 Test lock 文件。训练脚本默认拒绝读取 Test 标签。

### B8：数据质量审计与冻结

至少检查：

1. 任务总数、attempt 数和失败来源；
2. 有效 Checkpoint 数量和 split 分布；
3. Domain × Fault × Split 覆盖；
4. Continue 成功 / 失败数量；
5. Agent-visible feature 完整性；
6. Checkpoint / Restore hash；
7. Agent messages 恢复一致性；
8. 分支隔离；
9. Prompt / Completion Tokens 缺失率；
10. Model / Tool / Wall-clock Latency 缺失率；
11. Worker 和模型版本分布；
12. 原始轨迹 SHA-256；
13. Test 训练隔离守卫；
14. 敏感信息扫描。

数据最低 Gate：

- 有效真实 Checkpoint 总数 ≥120；
- Train / Validation / Test 最低 = 72 / 24 / 24；
- 目标总数 160；
- 目标 Train / Validation / Test = 96 / 32 / 32；
- Restore hash match = 100%；
- 关键模型成本字段可观测率 ≥95%；
- 所有风险特征都有来源和决策时刻可观测性证据。

如果数据不满足 Gate，报告具体缺口并决定是否扩充，不得直接宣布数据集冻结。

---

## 8. 干预分支：风险数据通过后再扩展

本节是风险预测数据之后的扩展，不是 B0–B4 的前置条件。

如果后续继续 RQ2–RQ4，必须从同一真实 Checkpoint 执行：

### CONTINUE

- 完整恢复 Agent messages 和环境状态。
- 不添加干预 Prompt。
- 正常继续执行。

### RETRY

- 只在安全且适用时执行。
- 只读请求可按协议重试。
- 写操作必须证明 `not_committed` 且具备幂等键。
- `committed` 或 `unknown` 禁止盲目重试。
- 每个 Checkpoint 最多 Retry 一次。

### REPLAN

- 从同一 Checkpoint 恢复。
- Qwen 只接收当前已知上下文和可见 Observation。
- 允许必要的状态刷新，但刷新动作和成本必须记录。
- 不得读取 expected answer、Verifier 隐藏信息或未来分支结果。
- 每个 Checkpoint 最多 Replan 一次。

### 分支隔离

同一 Checkpoint 的每个动作都必须：

- 独立 Restore；
- 独立 workspace；
- 独立 SQLite；
- 独立 RuntimeState；
- 相同初始 Agent messages；
- 相同初始环境 hash；
- 相同模型和 Prompt 版本；
- 记录 action applicability；
- 不适用动作标记 `not_applicable`，不当作失败或零成本。

### 效用扩展的数据门槛

现有指南建议 Retry、Replan 的 Train applicable 样本尽量各 ≥30；每个非 Continue 动作至少有 10 个成功和 10 个失败结果，才考虑训练状态条件结果模型。

这不是为了凑数而强制执行的目标。若某动作缺少负类，应如实报告数据不足，不得伪造标签。

---

## 9. 原始数据 Schema 建议

实际字段必须由 B1 冻结；以下是需要覆盖的信息，不要求机械照抄。

### 9.1 Task Attempt

```json
{
  "record_type": "task_attempt",
  "experiment_id": "phase2_real_qwen",
  "run_id": "unique_run_id",
  "task_run_id": "unique_attempt_id",
  "template_id": "template_x",
  "task_id": "task_x",
  "instance_id": "instance_x",
  "split": "train",
  "seed": 2026,
  "controller_type": "qwen3_model",
  "execution_mode": "model_backed",
  "model_id": "Qwen3-8B",
  "model_version": "record_actual_version",
  "worker_id": "worker_0",
  "prompt_version": "prompt_v2",
  "tool_version": "tool_v2",
  "verifier_version": "verifier_v2",
  "task_status": "success_or_failure_or_infra_failure",
  "failure_origin": "agent_or_tool_or_environment_or_model_service",
  "started_at": "ISO-8601",
  "ended_at": "ISO-8601"
}
```

### 9.2 Decision Checkpoint

```json
{
  "record_type": "decision_checkpoint",
  "checkpoint_id": "unique_checkpoint_id",
  "task_run_id": "source_attempt_id",
  "template_id": "template_x",
  "task_id": "task_x",
  "instance_id": "instance_x",
  "split": "train",
  "step_index": 3,
  "decision_trigger": "tool_observation_received",
  "controller_type": "qwen3_model",
  "execution_mode": "model_backed",
  "model_id": "Qwen3-8B",
  "worker_id": "worker_0",
  "messages_ref": "relative/path/to/messages.json",
  "runtime_state_ref": "relative/path/to/runtime.json",
  "environment_manifest_ref": "relative/path/to/environment.json",
  "latest_tool_observation_ref": "relative/path/to/observation.json",
  "observable_features_ref": "relative/path/to/features.json",
  "internal_fault_metadata_ref": "relative/path/to/internal_audit.json",
  "agent_state_hash": "sha256",
  "environment_state_hash": "sha256",
  "combined_state_hash": "sha256",
  "checkpoint_schema_version": "2.0"
}
```

### 9.3 Continue Outcome

```json
{
  "record_type": "continue_outcome",
  "branch_id": "unique_branch_id",
  "checkpoint_id": "unique_checkpoint_id",
  "action": "CONTINUE",
  "applicable": true,
  "controller_type": "qwen3_model",
  "execution_mode": "model_backed_branch",
  "source_state_hash": "sha256",
  "restored_agent_state_hash": "sha256",
  "restored_environment_state_hash": "sha256",
  "verifier_passed": true,
  "outcome": "success",
  "failure_category": null,
  "prompt_tokens": null,
  "completion_tokens": null,
  "tool_calls": 0,
  "model_latency_ms": null,
  "tool_latency_ms": null,
  "wall_clock_latency_ms": null,
  "termination_reason": "verifier_passed",
  "worker_id": "worker_0"
}
```

上述 Token / Latency 值仅为 schema 占位示例。真实未观测到的值必须写 `null`，真实未发生的成本才写 `0`。

### 9.4 风险训练样本

风险训练样本应由 Checkpoint + 决策时刻特征 + CONTINUE outcome 派生生成：

```json
{
  "record_type": "risk_example",
  "checkpoint_id": "unique_checkpoint_id",
  "template_id": "template_x",
  "task_id": "task_x",
  "split": "train",
  "feature_schema_version": "risk_features_v2",
  "feature_values": {
    "step_index": 3,
    "tool_call_count": 2,
    "observed_error_count": 1,
    "repeated_action_count": 0
  },
  "label": 0,
  "label_source_branch_id": "continue_branch_id",
  "label_source": "deterministic_verifier",
  "feature_cutoff_step": 3,
  "future_data_excluded": true
}
```

不要在原始 Checkpoint 中提前写入最终标签。标签应由分支结果派生，并保留 lineage。

---

## 10. 评估协议与统计要求

### 10.1 基线模型

至少比较：

- Constant prevalence baseline；
- Logistic Regression；
- LightGBM（仅当样本量和类别覆盖支持）。

先建立可靠的简单基线，不要因为模型复杂就假定 LightGBM 一定更好。

### 10.2 风险预测指标

- AUROC；
- AUPRC；
- Brier Score；
- Log Loss；
- Precision / Recall；
- Calibration Curve；
- Reliability Diagram；
- 各 Domain、Fault、Split 的分层指标；
- 置信区间。

AUROC 不能单独说明概率可靠性。风险预测要同时报告排序质量、概率误差和校准。

### 10.3 数据划分与置信区间

- 主要划分按 `template_id` 分组。
- 同一任务多个 Checkpoint 相关，统计时按 task/template 聚类。
- Bootstrap 应按 template 或 task 聚类，而不是把所有 Checkpoint 独立重采样。
- 只有一个类别的评估折，AUROC/AUPRC 记为不可定义，不伪造 0 或 1。
- 报告有效样本量、正负样本数、每个模板的 Checkpoint 分布。
- 不得在 Test 上调阈值或挑选特征。

### 10.4 风险预测结论判定

只有在以下证据基本齐备时，才可以说真实 Agent 风险预测获得支持：

1. 独立 Test 上优于常数基线；
2. AUROC / AUPRC 有合理改善；
3. Brier / Log Loss 不恶化，且概率校准有证据；
4. 删除注入器内部 metadata 后仍有预测能力；
5. 在不同领域和故障条件下不是完全失效；
6. 按模板聚类的置信区间支持主要结论；
7. 结果可以从原始轨迹和 Checkpoint 重新构建。

若只通过工程链路，不代表研究假设成立。若 Test 样本太少，应报告“证据不足”，不要写成“模型无效”或“模型有效”。

---

## 11. 算力、运行和资源安全

### 11.1 资源职责

- Qwen3-8B 推理：远程 vLLM Worker。
- Agent harness、工具、Checkpoint、Restore、分支：本地 WSL。
- 特征工程、Logistic、LightGBM、统计分析：本地 CPU 即可。
- 不需要为了风险模型训练额外占用 GPU。

### 11.2 GPU 启动规则

每次正式启动前：

1. 检查全部共享 GPU 的实时使用情况。
2. 连续两次采样确认目标 GPU 空闲。
3. 最多使用两张空闲 RTX 4090。
4. 记录 Worker 与物理 GPU 映射。
5. 没有合规空闲卡就停止等待。
6. 不得杀死其他用户进程。
7. SSH 超时后先检查远端 PID、端口和 Worker 状态，禁止盲目重复启动。
8. 只停止能够通过状态文件和命令行双重确认属于本次 run 的进程。
9. 结束后检查 GPU、Worker、端口和 SSH tunnel 是否释放。

不得使用 `verify=False`、`curl -k` 或关闭 TLS 校验解决连接问题。

### 11.3 采集前资源报告

正式批量采集前必须向用户报告：

- 计划任务数；
- 预计 Checkpoint 数；
- 预计 CONTINUE 模型调用数；
- 如果有干预扩展，预计 RETRY / REPLAN 调用数；
- 每个任务的最大 step / token / latency；
- 预计 GPU 占用时长；
- 本地磁盘预计增量；
- 失败续跑策略；
- 预计总耗时和停止条件。

未获得用户确认，不启动大规模采集。

---

## 12. 输出目录和版本管理

建议所有新数据使用独立 phase2 目录，不覆盖历史 Day 1–14：

```text
docs/phase2/
  real_agent_gap_audit.md
  real_agent_checkpoint_design.md
  real_agent_collection_plan.md
  protocol_risk_v2.md
  schema_risk_v2.md
  feature_allowlist.json
  daily_reports/
    b0_audit_report.md
    b1_protocol_report.md
    b2_checkpoint_report.md
    b3_continue_report.md
    b4_smoke_report.md
    b5_train_report.md
    b6_validation_report.md
    b7_test_report.md
    b8_data_freeze_report.md

runs/
  phase2_smoke/<run_id>/
  phase2_train/<run_id>/
  phase2_validation/<run_id>/
  phase2_test_locked/<run_id>/
  phase2_frozen/

data/
  raw_traces/
  decision_checkpoints/
  continue_outcomes/
  processed_risk/
```

实际目录可适配仓库现状，但必须保持原始数据、派生数据、模型和审计产物的边界。

每个正式 run 保存：

- `run_manifest.json`；
- 配置副本；
- catalog / split manifest；
- 原始轨迹 JSONL；
- Checkpoint manifest；
- Continue outcomes；
- model usage；
- Verifier outcomes；
- SHA-256；
- 资源释放状态；
- `formal_experiment` 标记；
- Gate 检查结果。

JSONL 只追加。重复运行使用新 attempt ID，不覆盖旧记录。派生风险数据必须保留原始 Checkpoint、特征生成器、标签来源和配置版本。

---

## 13. 新 Codex 会话执行规范

### 13.1 新会话第一轮必须执行的事情

1. 阅读 `AGENTS.md`。
2. 阅读 `PROJECT_GUIDE.md`。
3. 阅读 `NEXT_STAGE_EXPERIMENT_GUIDE.md`。
4. 阅读 `README.md`。
5. 阅读最近 Day 1–14 日报。
6. 阅读 `docs/phase2/audit_inventory.md`。
7. 阅读 `docs/phase2/risk_feature_audit.md`。
8. 阅读 P1-D1 / P1-D2 日报。
9. 检查 `pwd`、`git status --short`、最近 Git commit。
10. 检查当前 `.venv`、smolagents 版本和相关代码。
11. 核对当前远程 Worker 配置，但不启动、不停止服务。
12. 给用户报告 B0 现状审计结果。

### 13.2 Codex 不得自行决定的事项

以下内容必须先给出方案并等待用户确认：

- 更改风险标签定义；
- 更改决策时刻；
- 更改 F0–F6 故障语义；
- 更改任务模板和 split；
- 更改最低样本门槛；
- 修改 smolagents 执行循环的核心方式；
- 修改 Checkpoint / Restore 协议；
- 重启远程推理服务；
- 启动正式批量采集；
- 修改 Recovery-Bench 外部仓库；
- 删除、迁移或覆盖已有数据。

### 13.3 每阶段结束报告

每阶段保存日报，至少包含：

```markdown
# Workstream B - <Stage> Report

## Objectives
## Current Baseline
## Work Completed
## Files Changed
## Artifacts Generated
## Commands and Tests
## Real Qwen Evidence
## Checkpoint / Restore Audit
## Data Quality
## Resource Usage
## Acceptance Checklist
## Known Issues
## Remaining Work
## Status: PASS / CONDITIONAL PASS / BLOCKED
```

没有实际执行的测试写“未运行”。没有真实 Qwen 调用证据的任务不能标记为真实模型采集。没有完整 Agent 上下文恢复的 Checkpoint 不能计入有效样本。

---

## 14. 新 Codex 对话窗口启动 Prompt

将下面 Prompt 与本文件一起发给新 Codex：

```text
我新开了一个 Codex 会话，专门推进工作线 B：重新建立真实 Agent 风险预测数据。

项目根目录：
/home/three/projects/risk-aware-smolagents

请先完整阅读：
1. AGENTS.md
2. PROJECT_GUIDE.md
3. README.md
4. NEXT_STAGE_EXPERIMENT_GUIDE.md
5. WORKSTREAM_B_REAL_AGENT_RISK_DATA_GUIDE.md
6. docs/phase2/audit_inventory.md
7. docs/phase2/risk_feature_audit.md
8. docs/phase2/daily_reports/p1_d1_report.md
9. docs/phase2/daily_reports/p1_d2_report.md
10. Day 1–14 总结报告和相关代码

本会话只负责工作线 B，不负责 P1-D3 历史效用审计。

第一轮任务：执行 B0——现有实现与真实 Agent 风险数据链路审计。

请重点检查：
- 当前 smolagents 版本和 ToolCallingAgent 的真实执行循环；
- agent.run()、Agent Memory、messages、Assistant tool-call、Tool Observation 的生命周期；
- 当前 ExecutionMonitor 是否能准确记录 Agent 实际收到的 Observation；
- 当前 CheckpointManager 保存了什么、没有保存什么；
- 当前 Checkpoint 是否可以恢复完整 Agent 上下文并继续调用 Qwen；
- 当前 Reference Branch Runner 与真实模型驱动分支的差距；
- Fault Injector 是否向 Agent 返回真实错误响应；
- Deterministic Verifier 是否可以为 Continue 分支提供可靠标签；
- 当前 vLLM Worker、TLS、SSH tunnel 和资源管理脚本如何复用。

必须明确区分：
1. 真实 Qwen 执行；
2. deterministic_local_reference；
3. model-backed smoke；
4. formal model-backed experiment。

Day 10 的 20 条风险样本和满分结果不得作为真实 Qwen 风险预测证据。P1-D2 已确认存在 last_failure 注入元数据捷径；严格 Agent-visible 代理特征仍来自 synthetic history。

执行约束：
- 本轮只做只读审计和方案设计；
- 不修改代码；
- 不运行批量任务；
- 不启动或停止远程 vLLM / GPU；
- 不修改外部 Recovery-Bench；
- 不覆盖 Day 1–14 历史产物；
- 不泄露 API Key、证书私钥或敏感日志；
- 不凭文件名猜测实现，必须阅读实际代码和依赖版本。

请输出：
1. 当前真实 Agent 风险数据链路图；
2. 当前代码能力与缺口对照表；
3. 完整 Agent Checkpoint 的最小实现方案；
4. 如何保证 Observation 已进入 messages、下一轮模型调用尚未发生时捕获；
5. 如何从同一 Checkpoint 恢复并执行 CONTINUE；
6. 如何生成无泄漏的风险特征和标签；
7. 需要修改/新增的文件清单；
8. 单元测试、集成测试和 smoke 验收方案；
9. GPU、Token、磁盘和时间预算；
10. B0 Gate 结论及下一步建议。

请特别回答：现有 CheckpointManager 能否真正恢复 smolagents 的完整 Agent 推理上下文？如果不能，最小改动是什么？

第一轮完成后停止，等待我确认 B1/B2 的实施方案。不要自行开始代码修改或正式采集。
```

---

## 15. 工作线 B 的最终验收标准

最终不能只以“采集了多少条轨迹”作为成功标准。

必须同时满足：

| 维度 | 最终要求 |
|---|---|
| 真实性 | 所有正式风险样本来自真实 Qwen3-8B 执行 |
| 决策时刻 | Checkpoint 位于 Observation 进入 Agent 上下文后、下一轮推理前 |
| 可恢复性 | 完整 Agent messages + Runtime + Environment 可恢复 |
| 风险标签 | 来自同一 Checkpoint 的真实 CONTINUE 后续执行 |
| 验证器 | 由确定性 Verifier 产生结果 |
| 特征 | 仅使用决策时刻可观测信息 |
| 泄漏 | 内部故障元数据和未来结果不进入风险特征 |
| 数据划分 | 按 template 分组，Train / Validation / Test 严格隔离 |
| 成本 | Token、Latency 等字段真实记录，缺失与 0 区分 |
| 可复现性 | 原始 JSONL、配置、代码版本、hash 和派生脚本可追溯 |
| 独立评估 | Test 未参与训练、阈值选择和特征调整 |

**研究成功的关键不是获得一个很高的 AUROC，而是证明：在真实 Agent 执行过程中，决策时刻可见的历史证据确实包含能够预测后续 CONTINUE 失败的信息，并且这种预测能力能够在独立任务模板上复现。**

---

## 16. 给项目负责人的执行顺序

推荐严格按以下顺序推进：

1. **B0：只读审计。** 先确定完整 Agent 状态到底能不能捕获和恢复。
2. **B1：冻结 Protocol。** 把风险标签、特征、决策时刻和数据划分写死。
3. **B2：实现 Checkpoint。** 这是整个工作线最重要的工程 Gate。
4. **B3：实现真实 Continue。** 验证同一状态能够恢复并由 Qwen 接续。
5. **B4：小规模 Smoke。** 验证全链路和安全性。
6. **B5–B7：Train / Validation / Test 正式采集。**
7. **B8：数据质量审计与冻结。**
8. 数据冻结后，再进入风险模型重训与独立评估。
9. 风险数据链路稳定后，再扩展 RETRY / REPLAN 同状态分支和效用模型。

**最重要的停止条件：如果无法证明 Checkpoint 恢复了真实 Agent 的完整下一轮推理上下文，就停止正式数据采集。不要用环境快照代替 Agent Checkpoint。**
