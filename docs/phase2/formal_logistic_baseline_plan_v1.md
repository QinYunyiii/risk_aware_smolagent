# Formal Risk Baseline — 候选执行方案 v1

**PROPOSED_NOT_APPROVED_NOT_EXECUTABLE：本轮仅完成数据审核与训练准备，尚未拟合正式模型。**

执行候选参数集中在 `configs/formal_logistic_baseline_candidate_v1.yaml`。
协议允许在达到最低样本门槛且 Train 两类均存在时，做强正则、低维 Logistic 可行性基线；
这不代表已达到 LightGBM、稳定校准或正式泛化结论的样本条件。

## 数据入口

只使用已发布资格索引引用的 Train 95 条和新版 Validation 32 条主分支记录。
所有原始记录的资格字段保持不变，由派生索引明确允许范围。
Train 85 成功/10 失败，48 模板；Val 28 成功/4 失败，16 模板，模板互不重叠。
Train 的 1 条历史 null 保留为删失，不补为失败；19/7 重复不作独立训练行。
旧 Val BLOCKED 批、Smoke、Fixture、Test、其他动作均不进入数据快照。

新准备入口 `scripts/prepare_formal_baseline.py` 仅支持带可信 Manifest/资格索引 Hash 的
本地证明，运行时禁止真实网络。它先检查来源 Split/真实身份，再解析结果；核验记录链、
主 outcome/Checkpoint lineage，并从原 Source 重新计算 18 特征和 provenance。
快照分开保存 Train/Val，保留每行来源记录 Hash，不复制原始 Prompt 或响应。

标签方向明确为 `y_failure = 1 - y_continue`，模型正类是继续执行后的失败；
原 y_continue 和原风险行不改写。X 严格只含原 18 项，模板/任务/Run、故障分配、
Worker、策略版本、最终结果和未来成本均只作审计字段，不能进入模型。

## 下一步候选模型

1. 常数 Train 失败率基线，仅由拟合集的失败比例计算；CV 中用当前训练折比例。
2. 固定 L2 Logistic：C=0.1、lbfgs、max_iter=2000、class_weight=None、seed=20261128。
   不做参数搜索，不按 Val 表现改 C/特征/seed。采用较强正则是小样本可行性选择，
   不是声称这一参数已经验证最优。

原 18 个特征有 9 个在 Train 中为常量。候选预处理在当前训练集/当前 CV 训练折上
识别并移除常量，所有原始特征继续保存在快照，不回写定义。
其余数值特征按训练中位数补缺、训练均值方差标准化；全缺失列作为常量列删除并记录。
类别 `latest_observed_error_type` 用原可见错误解析器的固定词表，none 作参照项，未知类别拒绝。
不添加交互/多项式/隐含故障字段，不启用 no_progress_count/progress_delta/state_change_count。
本批特征无缺失；预处理规则为未来缺失情况预先定义，不会在准备阶段改写 null。

注意：9 个非恒定原始字段不等于编码后有效自由度；类别展开、重复/相关特征及罕见事件
必须在模型报告披露。正则化可行性拟合不能替代功效计算或充分样本。

## 拟合、验证和统计边界

- 只对 Train 拟合预处理及模型；Val 不参与权重、缩放或缺失填充值拟合。
- Train-only 3 折 StratifiedGroupKFold，template_id 分组，固定 shuffle/seed；全任务/分支
  不跨折。不因类别不足重抽 seed 或折数；单类验证折 AUROC/AUPRC 记 undefined，
  单类训练折记录不可估计，不替换模型或伪造 OOF。
- 常数模型和 Logistic 均报告 Train OOF 与独立 Val 结果；Train 全量重拟合仅生成最终
  Logistic 工程产物，仍不开放 Test。
- 阈值固定预注册 0.5；不在本轮搜索 Val 阈值或拟合后处理校准器。
- AUROC、AUPRC、Brier、Log Loss、Precision、Recall，5 个固定等宽校准箱及可靠性图。
  没有预测正例时明确分母和 zero-division 约定；不把 undefined 写成有效 0。
- 1000 次 template cluster bootstrap，seed 固定；报告单类重采样数量和有效统计次数，
  不按独立行 bootstrap，也不利用重复扩样缩窄置信区间。

Val 只有 4 个失败类，置信区间、阈值指标和分箱校准会很不稳定；先做可行性报告。
新版 Val 是工程修订后的完整 cohort，旧 Val 已用于工程诊断，需披露这一历史。
Train/Val 适用的故障策略版本不同，报告绑定各自 Manifest，不能把跨批变化视为因果收益。

## LightGBM 与旧训练器

LightGBM 继续禁用：Train 95<200、失败 10<30。旧规范还要求有效特征数/样本量检查，
目前配置没有给出具体函数；未来即使满足数量，也必须单独明确这一门槛，不能自动放行。
不降低门槛、不拿 Val、重复或旧 Smoke 凑样本；扩采须另立协议和授权。

旧 Day 10 流程面向工程样例，使用不同特征提取器和含内部 fault_* 的输入，且工厂同时
创建 Logistic/LightGBM。它的测试仍保留，但正式基线不会直接调用该训练入口。
后续需以新快照编写最小独立模型入口，严格限定 Train fit、原特征和 LightGBM gate。

## 下一阶段验收与停止条件

实施固定候选基线前审核本方案。获准后再实现并本地运行：证明预处理在 Train/当前折
内部拟合、模板隔离、正类方向、模型序列化后预测一致，以及未知/缺失/单类失败处理。
所有输出绑定本次数据快照、输入证明、参数、代码 Commit 和依赖版本，使用新 Run。
不覆盖历史，不根据 Val 重新选择样本，不打开 Test，不自动进入干预或策略实验。
