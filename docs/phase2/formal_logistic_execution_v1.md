# Fixed Formal Logistic Baseline v1

用户在基线候选方案交付后批准“好的，继续”，本版本授权实施并执行固定本地实验。
活动配置为 formal_logistic_baseline_v1.yaml；它与候选参数完全相同，仅改变审批状态。
旧候选、准备清单和数据快照不改。Test、LightGBM、远端请求、重采及干预均不在范围内。

训练正类为 continue_failure，即 1-y_continue。仅 Train 95 行拟合预处理/权重，
Train-only 3 折 StratifiedGroupKFold（template_id、seed20261128）；固定 C0.1、L2、
lbfgs、max_iter2000、无 class_weight；无搜索、无重采 seed/折数、无后处理校准拟合。
使用 sklearn 1.9 的 l1_ratio=0 表达 L2；旧 penalty='l2' 参数弃用，不改变正则含义。

常量识别、中位数、均值/方差仅在全 Train 或当前 CV 训练折拟合。18 个输入仍由原
allowlist 投影，none 作类别参照，其余固定错误类型独热编码；未知类别拒绝。
模板、故障分配、Run/版本、结果及未来成本均不进入 X。常数基线每折使用当前训练折
失败比例，全量预测只使用全 Train 比例。Val 仅评价，阈值固定 0.5。

单类验证折的 AUROC/AUPRC 为 null；单类训练折不拟合 Logistic、不填替代 OOF。
没有预测正例时 Precision 为 null，分母为 0；Recall 在真实正例数为 0 时为 null，
有正例却无预测正例时为 0。Log Loss 将概率裁剪至 [1e-15,1-1e-15]。
校准曲线使用 5 个固定等宽箱，空箱明确 null，不根据 Val 改箱。

1000 次 template_id cluster bootstrap 采用相同抽样索引配对比较模型；每次抽中模板
保留全部行及其重复抽中的整簇，不逐行 bootstrap。区间为 2.5/97.5 百分位，
基于固定预测，不在重采样中重新拟合模型。报告每指标有效/undefined 次数；
这不包含模型训练不确定性，不能当作精确泛化或因果收益区间。

run_formal_baseline.py 默认 local-preflight、不拟合。fit 模式另需 --authorize-fit、
绑定新清单的本地收据、可信固定快照、干净已提交代码和独立绘图解释器。
真实资格源在任何拟合前重新核验；只允许已审核快照 95/32，Test 入口不存在。
一次性 fit_claim 绑定本版 Manifest，输出目录独占，已有模型或结果不覆盖。

序列化后加载本次刚生成的模型，要求 Val 预测逐值一致。只从当前拥有目录加载 joblib，
不接受用户传入的任意 pickle。图表由独立 matplotlib 环境读取聚合分箱结果渲染；
训练环境版本不变，绘图依赖版本和安装日志单独留证。训练和渲染进程均禁止网络。

若失败，保留部分证据和 claim，不重跑、不调整参数。本版不保证模型优于常数基线；
Val 失败类仅 4 行且旧 Val 已用于工程设计，须披露宽区间和数据版本限制。
执行结果只用于可行性报告；Test 须在完整模型/阈值/校准决策冻结后另行授权。
