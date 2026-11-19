# P1-D2 风险预测特征捷径审计

## 结论摘要

Day 10 的满分主要由人工构造故障元数据造成，不能解释为真实 Qwen3-8B 风险预测
能力。

- 20 个样本全部来自 Day 9 `deterministic_local_reference` 的 Train Checkpoint，
  不是可独立评估的真实 Qwen 中间状态。
- `failure_response_received` 是 26 个特征中唯一与标签取值集合完全分离的单特征：
  9 个 Continue failure 全为 1，11 个 Continue success 全为 0。
- 全特征和 `injection_metadata_only` 的 Logistic/LightGBM OOF AUROC、AUPRC 均为
  1.0；原 Day 10 LightGBM 的 80 次 split 全部使用
  `failure_response_received`。
- 仅删除 `failure_response_received` 仍然满分，因为 `fault_*` 继续编码注入类型；
  仅删除 `fault_*` 也仍然满分，因为 `failure_response_received` 仍在。
- 删除 `last_failure` 派生的全部 8 个内部字段后，Logistic/LightGBM AUROC 降至
  0.631/0.646；严格 Agent-visible 五特征版本均为 0.591。
- 当前结果只是 20 个同源 Train 样本上的 grouped OOF 捷径诊断。没有访问官方
  Validation/Test，也没有改变标签、筛选样本、交叉验证折、阈值或超参数。

## 审计范围与不可变输入

- Day 9：`runs/day09/day9_20261119_080843_288841fc`
- Day 10：`runs/day10/day10_20261119_082058_aab1d057`
- 原模型：`models/risk/day10_20261119_082058_aab1d057`
- 配置：`configs/day10_risk.yaml`
- 数据：20 个 Train Checkpoint，9 failure / 11 success，20 个不同 template。
- 模型：原配置的 Logistic Regression 和 LightGBM；另外加入只使用训练折正类率的
  constant-probability baseline。

审计脚本复算出的全特征 OOF 概率与 Day 10 原始 40 条预测逐项一致，最大绝对差为
0。所有 Day 9/10 历史文件保持只读，新结果只写入 `runs/phase2_audit/`。

## 生成路径与代码证据

完整路径如下：

```text
Day 8 logical result
  -> Day 9 build_intervention_scenario(position)
  -> _failure() 生成 fault_type / response_received / error_type
  -> prepare_intervention_checkpoints() 构造 synthetic tool_history、last_failure、budget
  -> Checkpoint runtime_state.json
  -> Day 10 build_risk_dataset()
       feature: extract_risk_features(runtime, task.domain)
       label: int(not CONTINUE.success)
  -> StratifiedGroupKFold(template_id, 5 folds, seed=20261119)
```

关键证据：

- 26 个特征的固定 schema：`src/risk_agent/prediction/dataset.py:26-44`。
- `last_failure`、budget、history 到特征的计算：
  `src/risk_agent/prediction/dataset.py:68-118`。
- 标签来自未来执行完成后的 `CONTINUE.success` 取反，特征来自 Checkpoint runtime：
  `src/risk_agent/prediction/dataset.py:140-180`。标签没有进入特征矩阵，但用于监督标签。
- `response_received` 不是解析真实 Agent observation，而是 `_failure()` 按 fault type
  计算：`src/risk_agent/intervention/scenarios.py:72-86`。
- Day 9 场景由样本位置和任务工具确定：
  `src/risk_agent/intervention/scenarios.py:89-149`。
- 仅在有构造 failure 时增加一条 `tool_result=None` 的 synthetic history，并将
  `last_failure` 放入 `tool_state`：
  `src/risk_agent/intervention/scenarios.py:165-238`。
- Day 9 分支 controller 默认为 `deterministic_local_reference`：
  `src/risk_agent/intervention/runner.py:20-48`；执行路径是确定性本地工具和 verifier，
  不是 Qwen 继续推理：`src/risk_agent/intervention/runner.py:123-214`。
- 原 Day 10 fold 使用 template-grouped 5 折：
  `src/risk_agent/prediction/modeling.py:121-161`。

20 个 runtime 中 15 个有 `last_failure`，也恰好有 15 个非空 synthetic
`tool_history`；所有 `agent_memory` 只有 `system/user/controller` 三种 role，0 个包含
`tool` 或 `observation` role。因此当前数据不能证明 `last_failure` 或 synthetic
history error 曾作为真实工具响应展示给 Qwen。

## 26 个特征的来源与可见性

分类定义：

- `agent-visible`：原则上可以由 Agent 已看到的调用/错误历史重建；当前 Day 9
  仍只是 synthetic proxy，并非真实 Qwen 可见性证明。
- `simulator-internal`：决策前存在于 controller/runtime，但不是 Agent 消息中的
  原始可见字段。
- `injection metadata`：由实验故障构造器产生，正式部署风险模型禁止使用。
- `post-decision`：决策之后的 branch outcome、Verifier 结果或未来成本；当前 26
  个特征中没有直接 post-decision 字段，监督标签除外。

| # | 特征 | 完整来源/生成方式 | 信息类别 | 指南分组 | 部署允许 | strict visible |
|---:|---|---|---|---|---|---|
| 1 | `execution_progress_fraction` | `budget.tool_calls_used / (used+1)` | simulator-internal | agent_history | 是 | 否 |
| 2 | `tool_call_count` | `budget.tool_calls_used`；Day 9 按是否有 failure 设为 0/1 | simulator-internal | agent_history | 是 | 否 |
| 3 | `observed_history_count` | `len(tool_history)`；Day 9 为 synthetic 0/1 | agent-visible proxy | agent_history | 是 | 是 |
| 4 | `error_count` | history 中 `error_type != null`；error_type 复制自 `last_failure` | agent-visible proxy | observed_tool_response | 是 | 是 |
| 5 | `repeated_action_count` | 相邻 tool name + arguments signature 重复数 | agent-visible | agent_history | 是 | 是 |
| 6 | `no_progress_count` | `tool_result=null && error_type!=null`；Day 9 failure row 固定满足 | agent-visible proxy | observed_tool_response | 是 | 是 |
| 7 | `recent_latency_mean_seconds` | 最近 3 条 history 精确 latency 均值 | simulator-internal | agent_history | 是 | 否 |
| 8 | `recent_latency_max_seconds` | 最近 3 条 history 精确 latency 最大值 | simulator-internal | agent_history | 是 | 否 |
| 9 | `historical_tool_success_rate` | 有结果且无错误的 history 比例，空 history 默认 0.5 | agent-visible proxy | agent_history | 是 | 是 |
| 10 | `tokens_used` | `budget.tokens_used`，由 Day 8 step token 复制 | simulator-internal | agent_history | 是 | 否 |
| 11 | `token_budget_fraction` | `tokens_used/max_tokens` | simulator-internal | agent_history | 是 | 否 |
| 12 | `tool_budget_fraction` | `tool_calls_used/max_tool_calls` | simulator-internal | agent_history | 是 | 否 |
| 13 | `latency_budget_fraction` | `latency_seconds_used/max_latency_seconds` | simulator-internal | agent_history | 是 | 否 |
| 14 | `pending_operation_read_only` | controller 对 pending operation 的安全分类 | simulator-internal | agent_history | 是 | 否 |
| 15 | `failure_present` | `bool(tool_state.last_failure)` | injection metadata | injection_metadata | **否** | 否 |
| 16 | `failure_response_received` | `last_failure.response_received`，由 fault type 直接计算 | injection metadata | injection_metadata | **否** | 否 |
| 17 | `fault_f1_timeout` | `last_failure.fault_type == F1_TIMEOUT` | injection metadata | injection_metadata | **否** | 否 |
| 18 | `fault_f2_api_503` | `last_failure.fault_type == F2_API_503` | injection metadata | injection_metadata | **否** | 否 |
| 19 | `fault_f3_invalid_argument` | `last_failure.fault_type == F3_INVALID_ARGUMENT` | injection metadata | injection_metadata | **否** | 否 |
| 20 | `fault_f4_stale_observation` | `last_failure.fault_type == F4_STALE_OBSERVATION` | injection metadata | injection_metadata | **否** | 否 |
| 21 | `fault_f5_repeated_action` | `last_failure.fault_type == F5_REPEATED_ACTION` | injection metadata | injection_metadata | **否** | 否 |
| 22 | `fault_f6_partial_failure` | `last_failure.fault_type == F6_PARTIAL_FAILURE` | injection metadata | injection_metadata | **否** | 否 |
| 23 | `domain_data_analysis` | `TaskSpec.domain` one-hot | simulator-internal task context | agent_history/context | 是 | 否 |
| 24 | `domain_file_retrieval` | `TaskSpec.domain` one-hot | simulator-internal task context | agent_history/context | 是 | 否 |
| 25 | `domain_api_workflow` | `TaskSpec.domain` one-hot | simulator-internal task context | agent_history/context | 是 | 否 |
| 26 | `domain_calculation_logic` | `TaskSpec.domain` one-hot | simulator-internal task context | agent_history/context | 是 | 否 |

部署 allowlist 是除 8 个 `last_failure` 派生字段外的 18 个决策前特征。strict
Agent-visible 是其中更保守的 5 个历史统计：`observed_history_count`、`error_count`、
`repeated_action_count`、`no_progress_count`、`historical_tool_success_rate`。未来正式
Qwen 数据必须证明这些历史内容确实已呈现给 Agent；在此之前，它们只能视作
synthetic proxy，不能当作真实可见性已经成立。

## 固定交叉验证协议

所有 feature set 和 constant baseline 共用同一个 fold 对象：
`StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=20261119)`，group 为
`template_id`。复算 fold assignment 与原 Day 10 逐 Checkpoint 完全一致。

| Fold | Train n | Train failure/success | Validation n | Validation failure/success | Template overlap |
|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 7 / 9 | 4 | 2 / 2 | 0 |
| 2 | 16 | 7 / 9 | 4 | 2 / 2 | 0 |
| 3 | 16 | 8 / 8 | 4 | 1 / 3 | 0 |
| 4 | 16 | 7 / 9 | 4 | 2 / 2 | 0 |
| 5 | 16 | 7 / 9 | 4 | 2 / 2 | 0 |

每个验证折都有正负两类，因此本次每折 AUROC/AUPRC 均可定义。JSON 中保存全部
calibration curve；CSV 的 `scope=fold` 行逐 feature set、模型、fold 保存训练/验证
样本数、正负类数和 AUROC、AUPRC、Brier、Log Loss、Precision、Recall。若未来某折
为单类别，脚本会将 AUROC/AUPRC 写为 `null`，不会伪造为 0 或 1。

## 用户指定六组消融结果

以下是同一 20 个 Train 样本的整体 OOF 指标：

| Feature set | Model | n feat | AUROC | AUPRC | Brier | Log Loss | Precision | Recall |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `all_current_features` | Logistic | 26 | 1.000 | 1.000 | 0.012 | 0.094 | 1.000 | 1.000 |
| `all_current_features` | LightGBM | 26 | 1.000 | 1.000 | 0.004 | 0.068 | 1.000 | 1.000 |
| `without_failure_response_received` | Logistic | 25 | 1.000 | 1.000 | 0.028 | 0.145 | 1.000 | 1.000 |
| `without_failure_response_received` | LightGBM | 25 | 1.000 | 1.000 | 0.018 | 0.123 | 1.000 | 1.000 |
| `without_fault_features` | Logistic | 20 | 1.000 | 1.000 | 0.050 | 0.199 | 0.900 | 1.000 |
| `without_fault_features` | LightGBM | 20 | 1.000 | 1.000 | 0.004 | 0.068 | 1.000 | 1.000 |
| `without_all_internal_last_failure_metadata` | Logistic | 18 | 0.631 | 0.517 | 0.264 | 0.748 | 0.556 | 0.556 |
| `without_all_internal_last_failure_metadata` | LightGBM | 18 | 0.646 | 0.556 | 0.265 | 0.761 | 0.556 | 0.556 |
| `history_only` | Logistic | 13 | 0.682 | 0.563 | 0.211 | 0.584 | 0.583 | 0.778 |
| `history_only` | LightGBM | 13 | 0.677 | 0.565 | 0.238 | 0.706 | 0.583 | 0.778 |
| `strict_agent_visible` | Logistic | 5 | 0.591 | 0.520 | 0.197 | 0.554 | 0.600 | 1.000 |
| `strict_agent_visible` | LightGBM | 5 | 0.591 | 0.520 | 0.202 | 0.578 | 0.600 | 1.000 |
| Constant probability | Train-fold prevalence | 0 | 0.419 | 0.428 | 0.253 | 0.699 | 0.250 | 0.111 |

常数基线只使用每个训练折的 failure prevalence（四折为 7/16，一折为 8/16）预测
对应验证折，不读取验证标签。由于不同折的常数略有差异，合并 OOF AUROC 不必等于
0.5；它仍是无特征、无验证泄漏的基线。

指南要求的附加诊断结果：

| Feature set | n feat | Logistic AUROC/AUPRC | LightGBM AUROC/AUPRC | 含义 |
|---|---:|---:|---:|---|
| `observed_response_only` | 2 | 0.591 / 0.520 | 0.591 / 0.520 | 仅 synthetic `error_count/no_progress_count` proxy |
| `injection_metadata_only` | 8 | 1.000 / 1.000 | 1.000 / 1.000 | 诊断捷径，禁止部署 |
| `deployment_allowlist_only` | 18 | 0.631 / 0.517 | 0.646 / 0.556 | 与删除全部 `last_failure` 相同的保守部署输入 |

每折全部六项指标和 calibration points 位于机器可读 JSON；CSV 有 114 条指标记录
（9 个 feature set × 2 个模型 × overall+5 folds，加 constant baseline 的
overall+5 folds），JSONL 有 380 条逐 Checkpoint OOF 预测。

## 分组消融与重要性解释

- 原始全特征 LightGBM：80/80 split importance 来自
  `failure_response_received`。
- 去 `failure_response_received` 后仍满分；LightGBM 转而主要使用
  `fault_f3_invalid_argument`、`fault_f4_stale_observation` 和
  `fault_f2_api_503`，证明不是单一列问题，而是整组 injection metadata 问题。
- 去 `fault_*` 后仍保留 `failure_response_received`，LightGBM 又将 80/80 split
  放在该列。
- 删除全部 `last_failure` 后，LightGBM 使用 progress、tokens、history success 和
  domain；指标接近小样本噪声范围，且五折 AUROC 波动明显（约 0.17--1.0）。
- strict Agent-visible 的两个模型产生相同 0.591 AUROC；这仍受 synthetic history
  构造影响，不能解释为真实 Agent history 的预测证据。

本审计使用预先定义的分组消融作为主要解释方法。split importance 仅用于复核原始
捷径路径，不把 20 个样本上的单次 importance 当成稳定因果解释。

## 部署规则与后续数据要求

1. 正式风险模型永久禁止使用 `failure_present`、
   `failure_response_received` 和全部 `fault_*`。
2. `last_failure`、fault schedule、scenario ID、injection seed/phase 只能用于实验分层
   与审计，不能进入部署特征。
3. post-decision branch result、Verifier 结果、未来成本和未来工具结果继续禁止。
4. budget、latency、pending safety 和 domain 可以进入 monitor-side deployment
   allowlist，但必须由生产运行时在决策时刻生成，不能从注入器复制。
5. strict Agent-visible 特征只有在真实 Qwen message/tool observation 记录证明可见后
   才能升级为正式特征；当前 Day 9 synthetic history 不满足该证据标准。
6. 后续必须在真实 Qwen Checkpoint 和独立 Validation/Test 上重新评估，报告样本量、
   类别覆盖和置信区间。本次 20 个 Train OOF 结果不得用于宣称模型泛化。

## Gate

- [x] 26 个特征均有来源、生成路径、可观测性和部署许可说明。
- [x] 用户指定六组消融完成。
- [x] 指南要求的 observed/injection/deployment 诊断完成。
- [x] 所有模型使用同一 template-grouped folds，且与 Day 10 原 fold 完全一致。
- [x] 每折样本数、正负类数、六项指标和 calibration curve 已保存。
- [x] 常数概率基线已加入。
- [x] injection metadata 已从部署 allowlist 排除。
- [x] 只使用 Train OOF；没有访问 Validation/Test 或远程 Qwen。
- [x] 原 Day 9/10 产物未覆盖。

P1-D2 工程审计状态：**PASS**。研究结论：**不能据此支持真实 Qwen 风险预测能力**。
