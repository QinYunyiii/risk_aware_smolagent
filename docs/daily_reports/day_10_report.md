# Day 10 Report

## Objectives

- 以预先指定的 Continue 策略最终结果构造失败风险标签。
- 只使用 Checkpoint 决策时刻及历史信息生成风险特征，禁止未来泄漏。
- 训练 Logistic Regression 基线和 LightGBM 主模型。
- 按 `template_id` 分组进行 Train 内 out-of-fold 评估，固定阈值和超参数。
- 报告 AUROC、AUPRC、Brier、Precision、Recall 和校准数据。
- 不读取官方 Test，不提前实施 Day 11 效用估计。

## Local Environment Work

- 使用 Day 9 正式 run `day9_20261119_080843_288841fc` 的 20 个有效 Checkpoint。
- 风险正标签定义严格沿用指南：`CONTINUE` 分支最终失败；20 个样本中失败 9、成功 11。
- 特征只读取每个 Checkpoint 的 `runtime_state.json`，标签只从对应 Continue outcome 的 `success` 字段反转获得；两条读取路径在代码中分离。
- 固定 26 个数值特征，覆盖执行进度、工具次数、历史错误、重复动作、无进展、近期延迟、历史工具成功率、预算使用、pending operation 属性、当前失败元数据、故障类型和领域。
- 不使用 Continue 的工具结果、最终结果、验证结果、分支成本或任何 Retry/Replan 结果作为特征。
- 使用 5 折 `StratifiedGroupKFold`，分组键为 `template_id`；每折训练/验证模板交集均为空。
- 官方 Validation/Test 没有对应 Day 9 Checkpoint，因此没有伪造或重划官方 split；评估口径明确为 Train-only template-grouped OOF。
- 阈值预先固定为 0.5，模型超参数写入配置，未进行网格搜索、贝叶斯搜索或测试集调参。
- 项目 `.venv` 新增并固定：NumPy 2.5.5、SciPy 1.18.1、joblib 1.6.0、scikit-learn 1.9.1、LightGBM 4.7.0。

## Remote Environment Work

- 无。风险数据构造、训练和评估全部在本地 WSL CPU 完成。
- 未连接远程服务器，未启动 SSH 隧道、vLLM 或 Qwen3-8B，未占用共享 GPU。

## Work Completed

- 新增泄漏安全的风险样本 schema、固定特征提取器、来源 hash 和数据审计。
- 新增 Logistic Regression 标准化 Pipeline 和确定性 LightGBM 分类器。
- 实现模板分组 OOF 预测、逐折指标、均值/标准差、校准曲线和特征重要性。
- 在全部 20 个 Train 样本上拟合最终模型，并保存可反序列化的 joblib 产物和模型元数据。
- 保存 40 条 OOF 预测记录（20 个样本 × 2 个模型），每条包含模型、Checkpoint、task、template、标签、概率、预测和 fold。
- 两次相同 seed 的训练生成完全相同的 OOF predictions SHA-256：`30a00b473d643f74105b67e3d0938300da48099272f5bc5d426592d3f484e7ea`。
- 最终训练 summary 的全部 Day 10 工程 Gate 为 true，`passed=true`。

## Files Changed

- `.gitignore`
- `pyproject.toml`
- `requirements.txt`
- `requirements.lock`
- `configs/day10_risk.yaml`
- `src/risk_agent/prediction/__init__.py`
- `src/risk_agent/prediction/schema.py`
- `src/risk_agent/prediction/dataset.py`
- `src/risk_agent/prediction/modeling.py`
- `scripts/train_day10_risk.py`
- `tests/test_day10_risk.py`
- `docs/daily_reports/day_10_report.md`

未修改 Day 7 split、Day 8/9 原始产物、风险标签定义、评价指标、研究问题或本地/远程职责边界。

## Artifacts Generated

正式 run：`runs/day10/day10_20261119_082058_aab1d057/`

- `risk_dataset.json`：20 个决策时刻特征与 Continue failure 标签。
- `leakage_audit.json`：特征 allowlist、来源 SHA-256、样本和 split 审计。
- `oof_predictions.jsonl`：两模型共 40 条模板分组 OOF 预测。
- `metrics.json`：整体 OOF 指标、逐折指标及均值/标准差。
- `calibration_curves.json`：按 OOF 概率生成的校准点。
- `feature_importance.json`：标准化 Logistic 系数和 LightGBM split importance。
- `training_summary.json`：Day 10 Gate、口径和产物路径。

最终模型：`models/risk/day10_20261119_082058_aab1d057/`

- `logistic_regression.joblib`
- `lightgbm.joblib`
- `model_metadata.json`

配置完善前的预检 run `day10_20261119_082019_844db349` 及对应模型也按只追加原则保留；最终验收采用 `day10_20261119_082058_aab1d057`。二进制 joblib 已加入 `.gitignore`，元数据保留可审计。

## Commands and Tests

```bash
.venv/bin/python -m pip install \
  joblib==1.6.0 lightgbm==4.7.0 numpy==2.5.5 \
  scikit-learn==1.9.1 scipy==1.18.1

.venv/bin/python -m pytest -q tests/test_day10_risk.py
.venv/bin/python scripts/train_day10_risk.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另运行只读审计，验证模型可反序列化、概率范围、模型文件 SHA-256、两次 OOF 预测一致性，以及 `requirements.lock` 与 `.venv` 的冻结依赖一致性。

## Test Results

- Day 10 专项测试：3 passed。
- 全量回归：57 passed。
- 依赖：`No broken requirements found.`
- Python 编译：通过。
- 依赖锁：除项目自身 editable install 行外，`requirements.lock` 与 `pip freeze` 一致。
- 数据：20 个样本，9 failure / 11 success，20 个唯一 Train template。
- 特征：26 个固定数值特征；泄漏审计全部通过。
- 交叉验证：5 折；每折 16 train / 4 validation；模板交集 0/5。
- 官方 Validation/Test：未读取、未调参、未报告伪造指标。
- Logistic Regression OOF：
  - AUROC 1.0000；
  - AUPRC 1.0000；
  - Brier 0.011966；
  - Precision 1.0000；
  - Recall 1.0000；
  - 逐折 Brier 均值 0.011966，标准差 0.009131。
- LightGBM OOF：
  - AUROC 1.0000；
  - AUPRC 1.0000；
  - Brier 0.004261；
  - Precision 1.0000；
  - Recall 1.0000；
  - 逐折 Brier 均值 0.004261，标准差约 `5.54e-11`。
- 模型加载 smoke：两个 joblib 均成功反序列化并对 20 个样本输出 `[0,1]` 内概率。
- OOF 可复现性：两次运行的 predictions SHA-256 完全一致。
- 敏感信息：Day 10 配置、代码、日志和产物不包含 API Key、Authorization、密码或私钥。

## Acceptance Checklist

- [x] 训练 Logistic Regression。
- [x] 训练 LightGBM。
- [x] 标签为 Continue 策略下最终失败。
- [x] 特征只来自 Checkpoint 决策时刻及历史。
- [x] 不使用最终答案、未来工具调用、分支结果或未来成本作为特征。
- [x] 每折按模板隔离，训练/验证模板无交集。
- [x] 测试集未用于阈值、特征、超参数或成本权重选择。
- [x] 阈值和超参数预先固定，无超参数搜索。
- [x] 报告 AUROC、AUPRC、Brier、Precision、Recall。
- [x] 保存校准数据、逐折统计和 OOF predictions。
- [x] 模型概率位于 `[0,1]`。
- [x] 模型可保存、加载和复现。
- [x] 未提前实施 Day 11。

## Known Issues

- 本批只有 20 个 Train Checkpoint；官方 Validation/Test 尚无对应轨迹，因此当前指标是 Train 内模板分组 OOF，不是官方 Test 性能。
- Day 9 场景由本地确定性 reference controller 构造，不是 Qwen3-8B 自主轨迹分布。模型适用范围必须跟随该数据来源标记。
- `failure_response_received` 在当前构造数据中几乎直接区分 invalid/stale failure 与 transient/partial-success，LightGBM 的 80 次 split importance 全部落在该特征。两模型满分主要反映场景构造规律，不能解释为真实环境的泛化性能或因果效果。
- 样本量很小，每折只有 4 个验证样本；校准曲线和逐折方差估计不稳定。
- 24 个 Train 模板中只有 20 个具有 Day 9 有效 Checkpoint；Day 8 的 4 个基础设施失败没有被强行转成风险标签。
- 最终模型是在全部 20 个 Train 样本上拟合，用于后续工程集成；只有 OOF predictions 用于本日报的指标计算。

## Remaining Work

- Day 10 核心工程验收已通过。
- 正式泛化结论需要按既定模板 split 采集足够的 Validation/Test Checkpoint，保持 Test 只用于最终一次评估；不得将当前 OOF 满分描述为正式测试性能。
- Day 11 若使用 Day 9 utility 数据，必须继续显式处理动作适用性、controller 来源和动作 coverage，且不得用 Test 调成本权重。
- 等待用户确认后才进入 Day 11。

## Status: PASS

Logistic Regression 与 LightGBM 已完成固定配置训练；风险特征无未来字段、模板组隔离、官方 Test 未触碰，所需五项指标和校准数据均已生成。PASS 表示 Day 10 初始工程验收通过，不表示当前 20 个本地 reference Checkpoint 已足以支持真实 Qwen 或官方 Test 泛化结论。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
