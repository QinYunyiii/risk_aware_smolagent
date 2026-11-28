# Formal Baseline Preparation — 2026-11-28

## Objectives and Result

**BASELINE_PREPARATION_PASS**：完成本地资格数据审核、来源可追溯快照和固定基线候选方案。
本轮未拟合正式风险模型、未生成模型性能结果，也未运行 Qwen、远端预检或 Test。
开始 HEAD `c876c01`，工作区干净。用户批准继续下一阶段，按上轮剩余工作执行离线准备。

## Protocol Review

`protocol_risk_v2.md` 第 9 节及活动 v2.2 配置要求常数 Train 比例、强正则 Logistic，
LightGBM 至少 200 Train/每类 30，并满足有效特征数的样本门槛。
当前 Train 95 行（85 成功、10 失败），Logistic 最低样本可行性满足；LightGBM 明确 BLOCKED。
有效特征门槛的具体函数尚未定义，未来不能只满足数量就放行。

旧 Day 10 特征路径包含 runtime 内部 fault_*，不同于当前 18 项正式 allowlist，
且工厂同时创建 LightGBM。因此保留旧实现/测试，正式准备不调用旧数据/模型入口。

## Data Qualification and Export

新增 `src/risk_agent/prediction/formal_preparation.py` 和仅离线入口
`scripts/prepare_formal_baseline.py`。没有修改采集、Checkpoint 或任何冻结生产代码。

- 先校验输入证明 Hash、来源 Split/实际正式身份，再读取结果；拒绝 Test、Fixture、
  Smoke、其他 Split、重复引用、非法/缺失标签或不可信 Manifest。
- Train 资格仅按已发布 95 个 risk Hash 选择，保留 1 个历史 null 的删失说明。
  Val 只使用新策略通过后的正式资格索引 32 个引用，旧 BLOCKED 批不加入。
- **7204 个源 Artifact** Hash/长度复核；风险记录链、主 outcome/Checkpoint 关联、
  Run/研究身份一致，18 特征及 provenance 均从原 Source 重新计算匹配。
- 正类派生为 y_failure=1-y_continue，同时保留原 y_continue；原标签不改写。
  X 只按固定 allowlist 投影，故障、未来结果、任务/模板/Run 等作为独立审计字段。
- 输出不可覆盖的新快照，Train/Val 分开，保留风险/Checkpoint/outcome/features/provenance
  Hash。raw qualification 标志和资格索引不改写，未额外发布任何数据资格。

快照：本次审计目录下 `qualified_snapshot_final_20261128/`。
目录包含 train.jsonl、validation.jsonl、audit_summary.json、source_proof.json 和 Manifest。
中间 `qualified_snapshot_20261128/` 保留，不覆盖；最终门槛另明确数量与有效特征检查的区别。
最终快照 Manifest SHA-256：
`bd48e1b0b07d83574854e083a67226d47c79c8cbe8fd6cbc494fde76696c5e44`。
127 行有序导出 digest：
`18435bcbc8cc75d4d56220f75233fc78872d5cb5ef0e720fce9f2b51c419492c`。
新增 formal_baseline_preparation_manifest_v1.json，绑定准备代码、测试、候选参数、方案与
数据证明/快照 Hash，明确 model_fit_authorized=false、test_access_authorized=false。

## Actual Data Audit

| Split | 有效主样本 | 成功 / 失败 | 模板 | 缺失特征 |
| --- | --- | --- | --- | --- |
| Train | 95 | 85 / 10 | 48 | 0 |
| Validation | 32 | 28 / 4 | 16 | 0 |

64 个模板互不跨 Split；没有重复主 Task/Checkpoint。19/7 重复分支不作为独立行。
Train 删除的是资格索引已排除的历史 null，不是根据模型表现删除样本；null 不补成 0。

Train-only 常量 9 项：step_index、tool_call_count、observed_history_count、
remaining_step_budget、unique_tool_count、repeated_action_count、repeated_action_ratio、
tool_sequence_length、tool_budget_fraction。剩余 9 项有变化。
这是首个合格点采样的观测范围；不因此改为多 Checkpoint、不启用新进展特征。
常量判断只用 Train，没有利用 Val 标签或特征取值来选择列。

## Candidate Baseline Plan

新增 [候选执行方案](../formal_logistic_baseline_plan_v1.md) 和
`configs/formal_logistic_baseline_candidate_v1.yaml`，明确
**PROPOSED_NOT_APPROVED_NOT_EXECUTABLE、execution_authorized=false**。

候选为常数 Train 失败比例 + 固定 L2 Logistic（C=0.1、lbfgs、2000 次迭代上限、
class_weight=None、seed20261128），仅 Train/当前 CV 训练折拟合预处理及权重。
Train 3 折按模板分组、阈值固定 0.5、不搜索参数或阈值、不拟合后处理校准器；
按模板 bootstrap 并披露单类折、少数类和版本变化。Val 仅评价，Test 保持封存。
所有这些是后续待审核参数，尚未由本轮训练或性能选择验证。

## Tests

专项及旧预测模块回归 **29 passed，0 failed，0 skipped**，最终 1.24 秒：

- 仅 18 项、类别/次序/值 Hash 校验，未来/内部元数据不能进入投影。
- 数值 bool、NaN/inf、字符串拒绝，显式 null 不填零。
- 常量仅从 Train 定义；Test、重复主 Task/Checkpoint、模板重叠拒绝。
- 其他 Split/Fixture 在读取 Manifest/结果前拒绝，可信 Manifest Hash 不匹配拒绝。
- null/非法标签不重编码，重复资格引用在读取结果前拒绝。
- LightGBM 数量门槛和完整门槛分开；有效特征检查未实现时不能放行。
- 原 Day 10 3 项回归保留；其中训练仅使用测试生成的临时工程 Fixture，
  其模型产物不属于正式模型，也未使用本次真实数据。

真实数据快照通过同一纯本地入口重新生成并独立核验；不是以单元测试替代真实记录审核。
compileall、diff check 和历史完整性核验通过。只新增独立模块，不重复全仓 1176 项。
前置测试不冒充本轮结果。

主要实际命令：

```bash
.venv/bin/python <本次审计目录>/offline_test_guard.py tests/test_formal_baseline_preparation.py tests/test_day10_risk.py -q
.venv/bin/python scripts/prepare_formal_baseline.py --proof <本次审计目录>/input_proof.json --run-id qualified_snapshot_final_20261128 --output-root <本次审计目录>
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Artifacts, Resources and Git

本地审计：`runs/phase2_baseline_preparation/baseline_preparation_20261128_b841be51/`。
保留基线、可信输入证明、测试/命令日志、两个独立快照、最终完整性与 Manifest。
快照只留本地，不将特征/标签、原始轨迹、请求或凭据提交 GitHub。
**42697 个历史文件、422 个既有跟踪文件** SHA-256 未变，原完整冻结链通过。

本轮实验室 SSH、隧道、HTTP/Qwen、GPU/vLLM/Docker 操作和 Test 结果解析均为 0。
GitHub Git Push 仅用于脱敏代码/文档交付，与实验访问单独计。
未改变旧 Train/Val、旧 BLOCKED、claim、协议、阈值、任务或原资格索引。

本轮按仓库规范提交/推送新增模块、测试、候选配置和文档；实际 Commit 见 Git 历史。

## Remaining Work and Status

审核固定基线候选方案后，再实施独立拟合/评价入口并开展限定的本地基线实验。
不能复用旧 Day 10 隐藏故障特征，不打开 Test、不训练 LightGBM，不用重复或 Val 扩 Train。
只有通过下一阶段验收才会有正式 Logistic 性能结果；当前没有模型有效性或泛化结论。

**Status: PASS（仅离线数据审核与基线准备）**。
