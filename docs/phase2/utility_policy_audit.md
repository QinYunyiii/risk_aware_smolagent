# P1-D3 效用、成本与策略等价性审计

## 结论摘要

本审计从 Day 9 原始 `branch_outcomes.jsonl` 独立复算，并交叉检查 Day 11
训练集、模型和 OOF，Day 12 决策，以及 Day 13 的 180 条逐策略记录。审计工程
Gate 通过，但历史效用模型和策略实验**不足以支持研究有效性结论**：RETRY 与
REPLAN 没有失败样本，所有 Token 成本来自不调用模型的本地确定性分支，且
Full 与 Risk + Utility 实际完全等价。

机器可读结果位于：

- `runs/phase2_audit/action_coverage.json`
- `runs/phase2_audit/cost_observability.json`
- `runs/phase2_audit/policy_action_equivalence.csv`

复算入口为 `scripts/audit_p1_d3_utility_policy.py`。脚本只读历史产物，不训练
模型，不访问远程服务。

## 1. 审计范围与代码证据

| 审计点 | 实现证据 | 结论 |
|---|---|---|
| 不适用动作标签 | `src/risk_agent/utility/dataset.py::build_utility_dataset` | 仅 applicable 记录映射 outcome/cost；不适用记录四项均为 null |
| 单类别 fallback | `src/risk_agent/utility/modeling.py::_outcome_estimator` | 标签少于两类时使用 Beta-Bernoulli 常数估计器 |
| 常数成本 fallback | `src/risk_agent/utility/modeling.py::_cost_estimator` | 全相同标签使用 `DummyRegressor` |
| 工具计时 | `src/risk_agent/monitoring/execution_monitor.py::ExecutionMonitor.execute_tool` | `perf_counter` 只包围本地工具调用 |
| 分支 Token | `src/risk_agent/intervention/runner.py::InterventionBranchRunner` | 执行工具时显式传入 `total_tokens=0`，无模型调用 |
| 自适应策略 | `src/risk_agent/controller/policy.py::AdaptiveInterventionPolicy.decide` | 风险门控后按 success probability 减加权成本计算净效用 |
| 九策略工厂 | `src/risk_agent/evaluation/policies.py::build_experiment_policies` | Full 与 Risk + Utility 同为 `full(models)`；各消融语义见第 5 节 |
| Day 13 模型加载 | `scripts/run_day13_experiment.py` | 加载真实 Day 10/11 joblib，但在 Day 9 本地参考 Checkpoint 上执行 |

## 2. Day 9 动作标签覆盖

统计单位是 20 个 Checkpoint × 3 个候选动作，共 60 条候选记录。成功/失败只在
36 条 applicable 记录上统计。

| 动作 | 候选 | Applicable | Not applicable | 成功 | 失败 | 二分类条件结果建模 |
|---|---:|---:|---:|---:|---:|---|
| CONTINUE | 20 | 20 | 0 | 11 | 9 | 形式上两类；仅 20 条合成 Train 数据，不能据此主张泛化 |
| RETRY | 20 | 6 | 14 | 6 | 0 | 不支持；仍为 6/6 全成功 |
| REPLAN | 20 | 10 | 10 | 10 | 0 | 不支持；仍为 10/10 全成功 |

Not applicable 原因：

| 动作 | 原因 | 数量 |
|---|---|---:|
| RETRY | No failed operation is available to retry | 5 |
| RETRY | Refresh state before acting on a stale observation | 4 |
| RETRY | Retrying unchanged invalid arguments cannot make progress | 5 |
| REPLAN | No corrected plan or state-refresh operation is available | 10 |

原始分支文件把这 24 条未执行记录的 `success` 写成 `false`，但同时明确记录
`action_applicable=false` 和 `branch_created=false`。它们是“不适用”，不是观察到的
动作失败。Day 11 数据构建正确将这些记录的 outcome 和成本设为 null，没有把它们
送入训练。

CONTINUE 的 9 次失败分类如下：

| 场景 / 故障 | 错误 | 数量 |
|---|---|---:|
| invalid_argument / F3_INVALID_ARGUMENT | FileNotFoundError | 3 |
| invalid_argument / F3_INVALID_ARGUMENT | APIWorkflowError | 1 |
| invalid_argument / F3_INVALID_ARGUMENT | UnsafeExpressionError | 1 |
| stale_observation / F4_STALE_OBSERVATION | FileNotFoundError | 3 |
| stale_observation / F4_STALE_OBSERVATION | LogicToolError | 1 |

RETRY 和 REPLAN 没有 applicable failure，因此也没有可统计的失败类别。

## 3. Day 11 效用模型

### 3.1 实际训练与 OOF

Day 11 数据集有 60 条候选记录，只有 36 条 applicable 记录进入各动作模型。
`oof_utility_predictions.jsonl` 存在且覆盖全部 60 个 `(checkpoint, action)` key；
24 条不适用记录保留 null 预测。

| 动作 | 训练样本 | 正/负 | Outcome 模型 | OOF 概率范围 | 最终概率 |
|---|---:|---:|---|---|---:|
| CONTINUE | 20 | 11/9 | Logistic Regression | 0.048904–0.983138 | 状态相关输出 |
| RETRY | 6 | 6/0 | Beta-Bernoulli 常数 | 0.833333–0.857143 | 0.875000 |
| REPLAN | 10 | 10/0 | Beta-Bernoulli 常数 | 0.900000 | 0.916667 |

RETRY 的 OOF 两个数值仅由每折训练样本数不同导致，不是状态条件差异；REPLAN
每折验证预测均为 0.9。Beta fallback 的精确触发条件是某动作训练标签的唯一类别数
小于 2。

CONTINUE 确实拟合了接受状态特征的分类器，但 P1-D2 已证明关键特征含标签捷径，
且全部数据来自 20 个 deterministic-local Train Checkpoint。因此不能声称它学习了
可泛化的真实 Agent 状态差异。

### 3.2 成本模型

| 动作 | Tokens | Tool Calls | Tool Latency | OOF 加权成本 MAE |
|---|---|---|---|---:|
| CONTINUE | 常数 0 Dummy | 常数 1 Dummy | Ridge，20 个不同值 | 0.000272 |
| RETRY | 常数 0 Dummy | 常数 1 Dummy | Ridge，6 个不同值 | 0.000710 |
| REPLAN | 常数 0 Dummy | Ridge，标签 1/2 | Ridge，10 个不同值 | 0.134645 |

Token 模型全部为全零标签的常数预测。CONTINUE/RETRY 的 Tool Calls 也为常数。
REPLAN 虽有 1/2 两个 Tool Calls 值，但只有 10 个合成样本。Latency 是本地工具执行
耗时，不包含模型延迟或总 wall-clock。模型文件存在只证明训练管线运行过，不能证明
成本预测足够或研究意义有效。

## 4. 成本可观测性

分母为 36 条 applicable 分支；24 条 not applicable 不作为成本观测。

| 字段 | 观测数 | 可观测率 | 零值 | 状态解释 |
|---|---:|---:|---:|---|
| Prompt Tokens | 0/36 | 0% | 不适用 | 未分项采集；该执行模式无模型调用 |
| Completion Tokens | 0/36 | 0% | 不适用 | 未分项采集；该执行模式无模型调用 |
| Tool Calls | 36/36 | 100% | 0 | 本地真实工具调用计数，36 条均为正 |
| Model Latency | 0/36 | 0% | 不适用 | 该执行模式不产生模型调用 |
| Tool Latency | 36/36 | 100% | 0 | 本地工具调用计时，36 条均为正 |
| Total Wall-clock Latency | 0/36 | 0% | 不适用 | 历史 schema 不含该字段；不能用工具延迟替代 |

聚合字段 `extra_tokens` 在 36 条记录中确实全部为 0，但语义是分支 runner 不调用
模型并显式传入 0。这不是采集失败，也不是实际 Qwen 干预 Token 成本低。Prompt/
Completion 的细分成本以及真实模型延迟、总 wall-clock 均尚不可观测。

## 5. Day 13 策略等价性

### 5.1 实际动作与风险门控

| 策略 | CONTINUE | RETRY | REPLAN | 风险门控 |
|---|---:|---:|---:|---|
| vanilla | 20 | 0 | 0 | 不使用 |
| always_retry | 14 | 6 | 0 | 不使用 |
| rule_based | 5 | 5 | 10 | 不使用；读取 `last_failure.fault_type` 注入元数据 |
| risk_based | 11 | 0 | 9 | low 11 / high 9 |
| risk_plus_utility | 11 | 0 | 9 | low 11 / high 9 |
| full | 11 | 0 | 9 | low 11 / high 9 |
| without_risk | 11 | 0 | 9 | 固定 high 20 |
| without_utility | 11 | 0 | 9 | low 11 / high 9 |
| without_cost | 11 | 0 | 9 | low 11 / high 9 |

Day 12 的动作和风险门控与 Day 13 Full 在 20/20 Checkpoint 一致。

### 5.2 动作一致性矩阵

单元格是 20 个 Checkpoint 中选择同一动作的数量。

| policy | vanilla | always_retry | rule_based | risk_based | risk_plus_utility | full | without_risk | without_utility | without_cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vanilla | 20 | 14 | 5 | 11 | 11 | 11 | 11 | 11 | 11 |
| always_retry | 14 | 20 | 10 | 5 | 5 | 5 | 5 | 5 | 5 |
| rule_based | 5 | 10 | 20 | 14 | 14 | 14 | 14 | 14 | 14 |
| risk_based | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |
| risk_plus_utility | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |
| full | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |
| without_risk | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |
| without_utility | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |
| without_cost | 11 | 5 | 14 | 20 | 20 | 20 | 20 | 20 | 20 |

六个模型相关策略（risk_based、risk_plus_utility、full、without_risk、
without_utility、without_cost）在 20/20 Checkpoint 上动作完全相同。

### 5.3 Full、Risk + Utility 与消融的真实代码路径

- **Full vs Risk + Utility**：工厂分别创建两个对象，但都调用
  `AdaptiveInterventionPolicy(models=models, config=policy_config)`。20/20 动作、风险、
  分数、排除项和理由的完整 decision payload 相同，没有实际算法差异。
- **without_risk**：风险预测被常数 1.0 替代，真实 outcome/cost 仍调用。它把 Full 的
  11 个 low gate 改成 high，但最终动作 20/20 不变。
- **without_utility**：直接使用 `RiskOnlyPolicy`，不调用 outcome/cost，20 条
  `action_scores` 均为空；但动作仍与 Full 20/20 相同。
- **without_cost**：仍调用真实 outcome 和 cost 模型，只把返回的 weighted cost 置
  0。20/20 Checkpoint 的成本数值变化，但成功概率/净效用动作排序 0 次变化，动作
  0 次变化；预测的 cost component 仍完整保留。因此它关闭了“成本对决策的影响”，
  没有关闭成本模型计算。
- **risk_based**：只调用真实风险模型，高风险时用安全优先级选择 Replan/Retry；其
  动作也与 Full 20/20 相同。

逐 Checkpoint 的九策略动作、门控、成功概率排序、净效用排序和差异清单完整记录在
`policy_action_equivalence.csv`。15 个 Checkpoint 至少有一个简单基线不同于 Full；
另外 5 个所有策略均与 Full 相同。模型相关六策略没有逐点动作差异。

## 6. 有效性判断与不能支持的结论

### Day 11

**研究有效性不成立。** CONTINUE 模型受 P1-D2 捷径问题影响；RETRY/REPLAN 只能给出
单类别 Beta 常数；Token 和部分 Tool Calls 成本为常数；模型/总延迟缺失。现有结果
不能证明真实 Qwen Agent 的状态条件干预成功概率或完整干预成本可预测。

### Day 13

**仅工程执行可复现，策略实验研究有效性不足。** 180 条记录成对齐全，但全部来自
同一批 deterministic-local Train Checkpoint。Full 与 Risk + Utility 完全同路径，
三项消融虽在代码层改变对应信号或影响，却均未改变动作，无法识别风险、效用、成本
各自的因果贡献。

目前不能支持：

1. RETRY/REPLAN 结果模型学习到状态条件差异；
2. 零 Token 表示干预成本低；
3. 当前成本模型覆盖真实模型调用和端到端延迟；
4. Full 优于 Risk + Utility，或两者构成独立基线；
5. 风险、效用或成本模块分别改善策略选择；
6. Day 13 结果可泛化到真实 Qwen、Validation/Test 或未见模板；
7. 工程审计 PASS 等于研究假设成立。

## 7. P1-D3 Gate

**PASS（审计 Gate）**。动作适用性与结果已分离；单类别问题已量化；零 Token、缺失
字段、无模型调用和采集失败已区分；策略代码路径、实际动作与消融语义已逐点核验。
该 PASS 仅表示 P1-D3 审计完整、可重复，不认可 Day 11/13 的研究结论。
