# Formal Risk Error and Feature Coverage Audit — 2026-11-28

## Executive Result

**离线审计完成，Status: PASS；风险模型的预测效用仍为 NOT_ESTABLISHED。**

本次解释已冻结 Logistic 的误差和特征覆盖，不重训、不搜索参数或阈值、不打开 Test。
现有证据确认：首次合格决策点的 18 个字段中有 9 个 Train 常量；Train/Validation
可见错误类别支持不同；4 个 Validation 失败中有 2 个在捕获时没有可见错误。
这些是可复核的限制，尚不能确定样本量、特征表达、分布差异或模型容量各自的因果影响。

原基线 Validation AUROC=0.4375、AP=0.352713、Brier=0.113291、Log Loss=0.426025
保持原样。不能据 AP 高于常数基线就认定风险预测已有效，也不把审计 PASS 写成模型 PASS。
未发现足以据此改写标签、修复生产特征解析器或重跑实验的确定代码缺陷。

## Objectives and Scope

- 使用已发布的 Train 95 行、Validation 32 行及其原始 Source/CONTINUE 证据。
- 核对固定预测、失败排名、类别支持、常量/重复列和决策时刻可见信息。
- 给出下一步最小审查建议，不改变预算、捕获规则、标签、Split 或现有特征版本。

没有 SSH、HTTP、Qwen、GPU、vLLM、Docker 或额外采集。本轮正式模型拟合次数为 0。
单元回归中的临时合成模型与正式数据隔离，不是追加正式模型实验。

## Evidence and Integrity

开始 HEAD：`02f4b95db27c9593db79150c89025e0da04bcd12`，工作区干净。
独立审计目录：
`runs/phase2_formal_error_audit/error_audit_20261128_03ce4d5a/`。

冻结模型 Run：`runs/phase2_formal_baseline/formal_logistic_20261128_ec170bf0/`。
模型 Artifact Manifest SHA-256：
`885afc0e9d193e6d5a8c608fa6a1632d0312a760ff021d4955de53ca62648510`。
加载本地拥有的 joblib 前检查 Manifest 及全部 12 个文件的路径、长度和 SHA-256。
重新加载后的 32 条 Validation 预测与保存值逐值一致。

资格快照：
`runs/phase2_baseline_preparation/baseline_preparation_20261128_b841be51/qualified_snapshot_final_20261128/`。
快照 Manifest SHA-256：
`bd48e1b0b07d83574854e083a67226d47c79c8cbe8fd6cbc494fde76696c5e44`。
复用冻结加载器检查资格索引、7204 个源 Artifact、Source 特征重算和 provenance。
Train 95 行/48 模板/10 失败；Validation 32 行/16 模板/4 失败，模板不交叉。
重复分支未当作独立样本，历史 null 未补成失败，旧 BLOCKED Validation 未混入。

本轮结束重验开始时的 **47851 个历史文件和 440 个既有跟踪文件**，SHA-256 均不变。
原模型、预测、图表、fit claim、历史 Manifest、采集配置及特征定义不修改。
Test 结果未读取；完整正式数据集仍未就绪，LightGBM 仍不启用。

## Failure Ranks and Fixed Threshold

排名按失败概率从高到低；以下 4 个失败样本没有同分排名歧义。

| Validation 失败任务 | 失败概率 | 在 32 行中的排名 | 决策时刻可见错误 | 超过的成功样本数 |
| --- | --- | --- | --- | --- |
| api_order_shipped_read_002 | 0.266123 | 1 | invalid_argument | 28 |
| api_order_shipped_read_001 | 0.212918 | 11 | unavailable | 19 |
| p2_data_validation_control_001 | 0.026596 | 30 | none | 1 |
| p2_data_validation_timeout_002 | 0.026268 | 31 | none | 1 |

直接按正负样本成对比较：AUROC=(28+19+1+1)/(4×28)=49/112=0.4375。
AP=(1/1+2/11+3/30+4/31)/4=0.352713。第 1 个失败样本单独贡献 AP 的 0.25；
AP 与整体成对排序呈现不同侧面，不是计算冲突，也不证明其余失败均得到良好排序。

Validation 风险分数范围为 [0.025232, 0.266123]；Train OOF 为 [0.008731, 0.348498]。
因此固定阈值 0.5 下全部预测为非失败，Validation 4 个失败均漏报。
这是已保存分数和预设阈值的直接结果。本轮没有寻找“更好”的阈值，
降低阈值也不能自动解决当前排序及概率质量问题。

## Error Category Coverage

下表计数均为冻结的 `latest_observed_error_type`，失败指最终 Y_continue=0。

| 决策时刻类别 | Train 行数 / 失败 | Validation 行数 / 失败 |
| --- | --- | --- |
| none | 78 / 4 | 20 / 2 |
| other | 5 / 5 | 0 / 0 |
| no_progress | 8 / 1 | 4 / 0 |
| timeout | 2 / 0 | 3 / 0 |
| unavailable | 2 / 0 | 2 / 1 |
| invalid_argument | 0 / 0 | 3 / 1 |

`invalid_argument` 是预先声明词表中的合法类别，但 Train 没有对应训练样本；
冻结最终模型的该独热列系数为 0。其样本预测仍由其他字段和截距产生，
不能把这一覆盖空缺称为未知类别解析错误。

直接核对 5 条 Train `other` 的 Source 公开响应后，3 条是 unknown order，
2 条是请求完成情况未知、需先查询再写入的错误。该粗类别混合了不同公开语义，
但当前映射与冻结定义一致；本轮没有改成新特征或重标类别。
这 5 行只来自 3 个 Train 模板，不能把 5/5 当作跨任务普适失败规律。

Validation 的 3 条 invalid_argument 有 1 失败、2 成功；有错误不等于 CONTINUE 必败。
Train unavailable 的 2 行均成功，Validation 同类别 2 行中有 1 失败。
这些小样本差异支持“覆盖有限”的判断，不能单独证明分布漂移是唯一根因。

## Decision-Time Visibility and Future Outcomes

独立阅读原 Source 和 primary CONTINUE 轨迹，确认 2 个低分失败的时间边界：

- `p2_data_validation_control_001`：捕获时无可见错误；之后 final_answer 未通过 Verifier，
  后续工具调用没有记录错误。
- `p2_data_validation_timeout_002`：捕获时无可见错误；恢复后 filter_records 才记录
  InjectedTimeoutError，最终 Verifier=false。

另外两个失败在决策时已经有可见错误；一个后续出现 APIWorkflowError，
一个后续反复参数错误并耗尽预算。这里只使用历史未来轨迹解释已发生的结果，
**未来错误、隐藏故障配置、Verifier 及最终预算结果均不进入 X**。

前两项证明“显式错误信号可能在捕获之后才出现”，不能推出在当前 X 下风险不可预测。
公开任务/观察语义是否还包含有效信号需要单独审查，不能事后注入未来信息。
不据此把捕获点移至第二次 Observation 或故障发生之后。

## Feature Coverage and Redundancy

Train 9 个常量字段为：step_index、tool_call_count、observed_history_count、
remaining_step_budget、unique_tool_count、repeated_action_count、repeated_action_ratio、
tool_sequence_length、tool_budget_fraction。该批次在首次合格 Observation 捕获，
多步重复/连续错误等历史特征尚没有充分变化，这是采样与字段支持的实际关系。

变化字段中，Train 的 observed_error_count 与 consecutive_error_count 逐行相等；
recent_tool_latency 与 average_tool_latency 逐行相等。
删除常量和类别展开后为 14 列、编码矩阵秩 9，不能按 18 个字段宣称 18 个独立信号。

8 个变化数值字段中，elapsed_time_fraction、recent_model_latency 各有 1 个 Validation
值超出 Train 的最小最大区间，其余 6 个没有越界。该描述不判断越界显著性或因果作用，
也没有据此删掉延迟字段、改标准化、截断数值或选择模型。
审计另保存了冻结线性 logit 的逐项代数重构，仅用于核对计算，不作为因果特征重要性。

## What Is and Is Not Established

确认：字段常量/重复、类别覆盖空缺、四个失败排名、固定阈值漏报、两个失败的后发错误。
确认：现有模型加载和预测一致，资格及源特征链与冻结快照一致。
本轮没有证据支持将当前问题归因于 TLS、SSH、Checkpoint 恢复或模型文件损坏。

未确定：扩大 Train 是否足够、增加公开语义特征能否提高独立表现、模型容量是否不足、
Train/Validation 生产差异有多大影响。Train 只有 10 个失败、Validation 只有 4 个失败；
本轮未做重采样新实验、消融、反事实或新模型对照，不能声称已隔离上述原因。
本次事后类别/样本分析仅为探索性诊断，不替代预先冻结的主评估。

## Implementation, Commands and Tests

仅新增独立分析模块、离线入口和测试，不改原基线训练/生产/特征模块。
入口加载冻结模型进行 predict，并显式禁止 FormalLogistic、预处理器及 sklearn Logistic 的 fit。
源/预测标签、Split、风险记录 hash、唯一性或概率不合法即拒绝分析；Test 输入拒绝。

真实执行命令（proof 和输出根位于上述独立审计目录）：

```bash
PYTHONPATH=src:. .venv/bin/python scripts/audit_formal_risk_errors.py \
  --proof runs/phase2_formal_error_audit/error_audit_20261128_03ce4d5a/input_proof.json \
  --run-id descriptive_error_review_20261128 \
  --output-root runs/phase2_formal_error_audit/error_audit_20261128_03ce4d5a

.venv/bin/python runs/phase2_formal_error_audit/error_audit_20261128_03ce4d5a/offline_test_guard.py \
  tests/test_formal_error_audit.py tests/test_formal_logistic_baseline.py -q

.venv/bin/python -m compileall -q src/risk_agent/prediction/formal_error_audit.py \
  scripts/audit_formal_risk_errors.py tests/test_formal_error_audit.py
git diff --check
```

专项/相关回归 **33 passed，0 failed，0 skipped**，其中新增 12 项。
覆盖并列分数/单类 AUROC、支持差异、Train 区间、重复列、输入不变和血缘拒绝。
测试网络钩子拦截了一次 urllib3 导入时的 IPv6 本地 bind 能力探测，未发生连接或外部请求。
compileall、diff check、文件敏感内容扫描通过；未重复全仓回归，原生产模块无修改。

## Artifacts, Git and Resources

本地保留 input_proof、实际 argv、审计输出、Source/未来轨迹核对摘要、测试日志、
历史完整性核验及统一 Manifest。派生诊断 JSON 的独立 Manifest 另保留，历史数据不写入。
不上传个体预测、joblib、原始观察或大体积产物；Git 仅交付 3 个分析/测试文件及本报告。

本轮实验网络连接 0，正式 fit 0，参数/阈值搜索 0，Test 访问 0，新增采集 0。
没有创建需回收的远程进程或隧道。按 AGENTS.md 第 15 节，验收后自动 Commit/Push；
GitHub Push 是交付网络行为，与禁止真实实验请求分开记录。

## Remaining Work and Next Recommendation

建议下一步先做 **Train-only 决策时刻公开信号可用性审查**，仍不重训、不新增采集。
仅核对已有公开 prompt、工具 schema、工具参数及 Observation 能否表达当前粗类别遗漏的
语义，并检查所有候选值的决策时刻来源、覆盖率和无泄漏条件。
例如 unknown order 与完成情况未知只是本次发现的公开语义差异，尚未批准为模型特征。
下一阶段不得根据本次 Validation 四例设计专用 task_id 规则或反复用其筛选特征。

审查产物应为一份最小特征修订候选及 Train-only 验收标准；任何特征 allowlist、
采样、模型或阈值变更均另行审核、独立版本化，旧 18 字段基线和当前负面结果继续保留。
不同时改捕获点、Completion 预算和故障排程，不自动启用 LightGBM，不打开 Test。
本轮在报告和 Git 交付后停止。

## Status: PASS — offline audit only

误差与特征覆盖已按冻结证据解释。模型效用仍未建立；未声称已经找到或修复单一研究根因。
