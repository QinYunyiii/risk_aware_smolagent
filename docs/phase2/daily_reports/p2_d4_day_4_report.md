# P2-D4 Day 4 Report

## Objectives

在独立批准的范围内，以完全相同的 95 个 Train 样本、标签、三折和 Logistic 参数，
比较 v1 与增加 public_completion_unknown 的 v2，报告配对 OOF 和模板 Bootstrap。
不访问 Validation/Test，不调参数/阈值，不拟合全 Train 生产模型，不进入采集或干预实验。

**Status: PASS — comparison execution and audit only。**
**研究结果：本次未观察到 v2 增量收益，四项主要指标点估计均退步。**
不改变 Fold、参数、标签、样本或字段定义来消除负面结果。

## Initial State and Git

- 初始 HEAD：`071ee04234447127c3e6e1caba5a4b23be6b6de4`，工作区干净。
- 用户在 Day 3 结束、明确请求独立模型比较批准后回复“继续day4”。
- 单独批准记录：`docs/phase2/approvals/p2_d4_day4_oof_approval_v1.json`，范围仅配对 Train OOF。
  没有修改 Day 2/3 的候选状态或旧记录中的关闭授权开关。
- 实现、专项测试、无正式数据运行时预检及零拟合的数据/Fold 预检先通过。
- 在观察正式比较指标之前冻结实现 Commit：
  `7255f60a02856851e2a9e358d00a8f8ca01fcd1b`。
- 正式比较从该提交的干净工作区启动，仅一次正式比较调用；其后只做无拟合独立审计。

## Data Sources

专用 Train proof、两个正式 Train Manifest 和原 Eligibility Release 按 Day 1 边界重新核验；
不调用 mixed snapshot/prepare_rows，不读取历史 Train+Validation 混合预测文件。
Train 标签取自资格绑定的风险记录，正类仍为 y_failure=1-y_continue。

Day 3 Sidecar Run：`p2_d4_day3_20261128_8b5cfd26`。
其 Manifest SHA-256：
`e84261247f8b8cf78b23a8ca63c5c3517fc108278a603780a01187cdc81fd943`。
只使用合格 95 条 Sidecar；逐条核对记录 Hash、risk/CP 身份、原 18 值、截止点及来源。
19 个重复分支和 1 个删失未纳入，任何 UNKNOWN、重复、错配或原值变化都会拒绝比较。

候选 Manifest SHA-256 保持：
`2517ef42fde46f1a55ba32e6278678dfd170bdd0b44047960fb27c2174f4cd26`。

本轮执行 Manifest：`docs/phase2/p2_d4_day4_paired_oof_manifest_v1.json`，SHA-256：
`8554f5a5559092793428522ac5416688fb7303a0699be820d08af0f5064661cb`。
绑定 29 个原依赖/新配置/批准/实现/测试文件；不改历史清单。

## Local Environment Work

本地 CPU，小样本 6 次拟合，数值线程固定为 1；没有安装/升级依赖。
numpy2.5.5、scipy1.18.1、sklearn1.9.1、joblib1.6.0，实际版本与冻结配置相符。
使用 `-I -S -B`、Day 1 纯源码装配和精确文件 ReadPolicy，数据仍默认拒绝。

数值运行时为新阶段单独登记必要包的代码/扩展、三份 sklearn CSS、包版本/entry_points 元数据；
不放行虚拟环境整个目录、任意 JSON 或实验数据目录。缺失元数据探测按精确路径登记，
执行期间若原本不存在的支持文件突然出现则拒绝。

threadpoolctl 需要只读 `/proc/self/maps` 枚举当前进程的数值库。新增 Day 4 专用策略仅允许
此精确系统路径，并验证其指向本 PID；不允许其他 proc、写入或任意符号链接。
该信息仅用于数值后端运行，不进入特征，不保存内核映射原文。旧 Day 1 策略未修改。

## Remote Environment Work

共享服务器 SSH、隧道、API/Qwen、GPU/vLLM、Docker 和轨迹采集操作均为 0。
GitHub Push 仅为完成验收后的代码交付，与审计进程禁止网络/子进程分开记录。

## Implementation

### 严格配对与预处理

仅比较两个实验臂：v1 原 18 项；v2 同样的 18 项加一项 completion_unknown。
v1 直接调用未修改的 FormalLogistic/FormalPreprocessor；v2 组合原预处理器，
只对新增位在当前训练折内检查常量、计算均值/标准差，再追加一列。
未知值拒绝；常量新增位应删除，不能根据留出折的变化决定保留。

每折两个实验臂的旧字段常量清单、中位数、均值、标准差和 fit_task_ids 均逐项相同。
预处理只 fit 当前训练折，held-out 仅 transform。身份/来源 Hash/模板字段不进入 X。

固定 L2、C=0.1、lbfgs、max_iter=2000、class_weight=None、random_seed20261128。
与原 v1 一致，sklearn1.9 以 l1_ratio=0 表达 L2，没有改变正则含义。
StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=20261128)，template_id 分组。
划分生成一次供两臂共用；不可估计折如实记录，禁止重抽 seed/Fold 或替换数据。

固定阈值 0.5，五个等宽可靠性分箱；无参数搜索、阈值搜索、校准器或 LightGBM。
不做全 Train 最终拟合，不覆盖或更新历史生产模型。

### 唯一运行与审计

配置在指标出现前冻结正式 Run ID；比较模式拒绝其他 Run ID，目录必须新建。
数据通过后独占创建 fit_claim，固定最大六次拟合，无自动重试。
逐 Fold/实验臂保存 started/completed 独占收据，记录预处理、参数和 OOF 来源。

Bootstrap 1000 次、seed20261128、template_id 整簇有放回抽样；两个实验臂使用同一索引。
每次先计算两组指标再计算 v2-v1；差值区间来自配对差值分布，不是两个独立区间相减。
模型不重新拟合，区间只条件于本次固定 OOF 预测，不包含训练不确定性或因果效果。
单类 AUROC/AP、无预测正类 Precision 等保留 undefined，并记录有效次数。

## Files Changed

- `configs/p2_d4_day4_paired_oof_v1.json`：两臂、参数、Fold、Bootstrap、运行 ID 与数值环境。
- `docs/phase2/approvals/p2_d4_day4_oof_approval_v1.json`：仅 Day 4 的独立批准。
- `docs/phase2/p2_d4_day4_paired_oof_manifest_v1.json`：29 项依赖 Hash。
- `src/risk_agent/prediction/paired_train_oof.py`：v2 组合预处理、同 Fold 比较和配对 Bootstrap。
- `src/risk_agent/prediction/train_oof_inputs.py`：Train 资格与 Sidecar 一一对应加载。
- `src/risk_agent/prediction/train_oof_runtime.py`：精确数值依赖和受限系统读取。
- `scripts/compare_train_features_v2.py`：预检、唯一比较、拟合生命周期和产物发布。
- `tests/test_paired_train_oof.py`：23 项新增专项。
- 本日报。

既有生产代码、配置、候选/回填清单、Sidecar、原始标签/数据/模型均未修改。

## Artifacts Generated

正式 Run：`p2_d4_day4_20261128_1c53d8a7`。
目录：`runs/phase2_p2_d4_day4/p2_d4_day4_20261128_1c53d8a7/`。

- oof_predictions.json：95 行 × 2 预测、标签、Fold 和原 risk/Sidecar 引用。
- fold_models.json：相同划分、训练/留出身份与模板、失败数、新位分布、预处理和模型参数。
- bootstrap_draws.json：1000 个共同模板抽样及两臂/差值指标。
- summary.json：主要指标、混淆矩阵、可靠性分箱、区间、估计范围。
- fit_claim.json 和 12 个拟合生命周期收据：恰好 6 started、6 completed。
- source_proof.json、access_policy.json、access_audit.json、integrity.json、run_context.json。
- artifact_manifest.json：上述 22 个成员的长度和 SHA-256。

Artifact Manifest SHA-256：
`44f5654f94139563cd615755789722a442d182563307a0bcfe9d266c2bcf18f5`。
测试、预检、独立核验和 Git 交付证据另存新的 `runs/phase2_p2_d4_day4_delivery/` 目录。
原始个体预测、Bootstrap 抽样、Fold 参数和完整日志不上传 Git。

## Commands and Tests

```bash
# 实际使用本轮离线测试监督器运行
.venv/bin/python -m pytest -q tests/test_paired_train_oof.py \
  tests/test_formal_logistic_baseline.py tests/test_feature_v2_candidate_contract.py \
  tests/test_train_feature_backfill_v2.py tests/test_train_only_access.py

# 唯一正式比较：实现冻结后执行
.venv/bin/python -I -S -B scripts/compare_train_features_v2.py \
  --manifest-sha256 8554f5a5559092793428522ac5416688fb7303a0699be820d08af0f5064661cb \
  --mode compare --run-id p2_d4_day4_20261128_1c53d8a7

.venv/bin/python -m compileall -q scripts/compare_train_features_v2.py \
  src/risk_agent/prediction/paired_train_oof.py src/risk_agent/prediction/train_oof_inputs.py \
  src/risk_agent/prediction/train_oof_runtime.py tests/test_paired_train_oof.py
git diff --check
```

**153 passed，0 failed，0 skipped**：23 个新增专项、130 个 v1/合同/Sidecar/读取保护回归。
覆盖两臂相同 Fold 与基础预处理、训练折内常量/缩放、常量新增位的完全相同预测、
UNKNOWN/非整数和非 Train 拒绝、不可估计折不重抽、非等长模板整簇/配对 Bootstrap、
单类/空分母、协议漂移、Sidecar 标签关联/Hash，以及受限 proc 例外仍拒绝其他 proc 和符号链接。
临时合成数据拟合不计入本轮正式六次拟合，也不作为正式指标。
离线测试监督器记录并拦截了 1 次 `socket.bind` 事件，没有放行外部网络连接；
这与正式比较和独立数据审计的读取拒绝事件为 0 分别记录。

运行时预检包含一次四行合成数据拟合，检查数值库的延迟读取，不读 Train；正式拟合数为 0。
数据预检读取同一 95 行并生成三折，正式拟合数为 0。早期预检对缺失的可选包元数据、
entry_points 探测按 BLOCKED 保留；在指标出现前明确运行支持角色后预检通过。
未放宽实验数据读取，也未改模型参数。正式比较仅一次，没有参数试跑或重训。

compileall、空白检查、敏感信息扫描通过。未重复全仓测试。

## Quantitative Results

### 主要指标

正类为 CONTINUE 失败，95 行/10 个正类。AP 为 Average Precision。
差值均为 v2-v1；AUROC/AP 越大越好，Brier/Log Loss 越小越好。

| 指标 | v1 OOF | v2 OOF | 差值 | 配对差值 95% 区间 |
| --- | --- | --- | --- | --- |
| AUROC | 0.665882 | 0.647059 | -0.018824 | [-0.070660, 0.019490] |
| AP | 0.536747 | 0.272593 | -0.264154 | [-0.469336, 0.002483] |
| Brier | 0.080368 | 0.088270 | +0.007903 | [-0.000145, 0.019875] |
| Log Loss | 0.304762 | 0.326130 | +0.021368 | [0.000413, 0.052851] |
| Precision | undefined | undefined | undefined | 0 个有效、1000 个 undefined |
| Recall | 0 | 0 | 0 | [0, 0]，998 个有效 |

v1 结果与既有 Train OOF 基线吻合，没有读取历史混合预测文件。
两臂在固定 0.5 下均 predicted_failures=0，混淆矩阵均为 TN=85、FP=0、FN=10、TP=0。
Precision 分母为 0，不写成有效 0；Recall 分母为 10，值为 0。

Bootstrap 的 2/1000 次抽样无正类，AUROC/AP/Recall 及相应差值为 undefined；
Brier/Log Loss 有效 1000 次。两臂及差值完整区间与次数留在原始 summary。
Log Loss 差值区间在本次条件分析中高于 0；其他主要差值区间跨 0。
这些探索性、多指标、固定预测下的区间不包含训练不确定性，不作普适因果或泛化结论。

### Fold 与新增字段

| Fold | 训练/留出行 | 训练/留出失败 | 新位训练/留出正值 | 新位删除 | v2 新位标准化系数 | 矩阵秩 v1/v2 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 63 / 32 | 6 / 4 | 2 / 2 | 否 | -0.143929 | 9 / 9 |
| 2 | 64 / 31 | 7 / 3 | 4 / 0 | 否 | +0.121791 | 9 / 10 |
| 3 | 63 / 32 | 7 / 3 | 2 / 2 | 否 | +0.142160 | 7 / 8 |

三折训练均有变化、都能保留拟合，没有通过更换划分避开不可估计问题。
但首折编码列增加而矩阵秩未增加，说明“有变化”不等于提供独立信息。
标准化系数方向不一致，不作为因果重要性或跨折可直接比较的效果量。

4 个正值仅来自两个模板，当前模板内均不变化。Fold 1 的训练正值来自一个模板、
对应标签均成功；留出正值来自另一模板、标签均失败。Fold 3 正好相反。
这是稀疏模板支持及标签关系不稳定的直接证据，与系数符号变化和负面结果相符；
没有据此证明它是唯一原因，也没有把模板 ID 加入预测器。

全部 95 行的预测概率变化，最大绝对变化为 0.174537。
排序也变化：AUROC/AP 下降，不是只发生概率尺度改变。

### 固定可靠性分箱

| 分箱 | v1：行数 / 平均预测 / 失败率 | v2：行数 / 平均预测 / 失败率 |
| --- | --- | --- |
| [0, 0.2) | 81 / 0.064166 / 0.049383 | 84 / 0.067779 / 0.071429 |
| [0.2, 0.4) | 14 / 0.269474 / 0.428571 | 9 / 0.252598 / 0.444444 |
| [0.4, 0.6) | 0 / null / null | 2 / 0.427110 / 0 |
| [0.6, 0.8) | 0 / null / null | 0 / null / null |
| [0.8, 1] | 0 / null / null | 0 / null / null |

两臂箱成员不同，不能挑某一箱较接近对角线就宣称校准改善。
整体概率误差和 Log Loss 变差，没有支持 v2 校准改善的证据。

## Data Integrity Audit

正式运行重验 **599 个授权输入 SHA-256**，读取拒绝事件 0。
**48,042 个历史文件大小/mtime 无变化**；其他历史文件未重读内容作全量 Hash，
继续遵守用户选择的边界。候选/回填清单、标签、Sidecar、原模型及源码均未修改。

独立核验进程只使用标准库，不调用模型 fit，也不复用生产指标函数来核对自身结果：

1. 重新关联 95 行的原 risk label、Sidecar 和来源 Hash。
2. 检查模板互斥、每行一次 OOF、训练/留出身份与失败数。
3. 重算训练折中位数、均值、总体标准差及新增位的常量判定。
4. 从 Fold 权重/截距和预处理重建 **190 个 OOF 概率**，与保存值一致。
5. 按成对比较重算 AUROC、按唯一分数阈值重算 AP、按公式重算概率误差与分箱。
6. 从共同模板抽样重建全部 **1000 次 Bootstrap** 的两臂指标与差值，
   独立计算分位数/undefined 次数，与保存区间一致。
7. 检查唯一 fit_claim 和六对 started/completed 收据。

独立审计没有重新训练；重验 622 项授权 Hash（原输入加本轮产物），拒绝事件 0。
原始 Artifact Manifest 的 22 个成员独立验长度/Hash 全部匹配。

## Acceptance Checklist

- [x] 两臂同一 95 行、标签、原值和 Fold。
- [x] 训练折内预处理，新位常量与缩放可追溯。
- [x] 三折均可估计，6 次拟合完成，无重抽或自动重试。
- [x] 每行一组配对 OOF，来源与预处理/模型参数完整。
- [x] 1000 次模板整簇共享抽样，个体及差值区间通过独立核验。
- [x] 固定阈值、混淆矩阵、可靠性分箱及 undefined 正确报告。
- [x] 未访问 Validation/Test，未调参、校准、最终全量拟合或启用 LightGBM。
- [x] 明确报告没有增量收益，保留负面结果。
- [x] 未进入 Day 5、新采集或干预实验。

## Known Issues

- Train 只有 10 个失败、新位只有 4 正值/2 模板，当前不支持可靠的跨模板增益主张。
- 无变化列虽已按训练折处理，剩余矩阵仍可能冗余；L2 系数不代表独立因果效应。
- Bootstrap 固定 OOF 预测，不覆盖重新拟合和分折的不确定性；没有多指标显著性校正。
- 同 Checkpoint 重复中的标签差异与历史删失未因本比较解决，仍保留原资格边界。
- 数值运行时支持是明确新增的 Day 4 角色，不是放宽未来实验数据读取；Python Hook 仍非恶意原生代码沙箱。
- 当前负面结果不证明公开完成状态在所有任务或安全决策中无价值，只是不支持本次预测器推广。

## Remaining Work

建议保留 v1 为现有基线，不把 v2 推广为风险预测生产特征，不改 C、阈值、Fold 或字段定义来补救指标。
v2 Sidecar 继续作为独立、可追溯的研究产物保存。

等待审核后可进入 Day 5 的 Train 扩充方案设计，重点预注册独立实例及模板内/跨模板的公开信号覆盖，
不根据模型分数或已知标签选择性补样，不把重复分支当新样本。
采集计划仍需单独批准；本轮不生成新实例、不采集，也不修改 Checkpoint 策略。

## Status: PASS

实验执行和审计通过；v2 增量收益未观察到。没有把工程 PASS 等同研究假设得到支持。

## Git Commit

正式执行 Commit：`7255f60a02856851e2a9e358d00a8f8ca01fcd1b`，执行时工作区干净。
本脱敏日报在验收后追加，按 AGENTS.md 第 15 节提交并推送 origin/main。
最终交付 Commit/Push 结果另存收据并在回复中报告；个体数据、Fold 参数、抽样与原始日志仅本地保留。
