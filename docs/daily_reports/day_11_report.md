# Day 11 Report

## Objectives

- 以 T-Learner 结构分别训练 Continue、Retry、Replan 的条件成功概率模型。
- 分动作训练 Token、Tool Calls、Latency 成本模型并保证预测非负。
- 显式排除不适用动作，不将其记成失败或零成本训练标签。
- 按模板分组生成 OOF outcome/cost predictions。
- 报告 Outcome Brier/MAE、Cost MAE、Utility MAE 和 Policy Regret。
- 不读取官方 Test，不提前开发 Day 12 在线策略控制器。

## Local Environment Work

- 使用 Day 9 正式 run `day9_20261119_080843_288841fc` 的 20 个 Checkpoint、60 条候选动作记录。
- 保留 36 条适用动作作为训练/评估样本；24 条不适用动作保留原因但将 outcome、cost 和 prediction 置空。
- 动作覆盖：Continue 20/20、Retry 6/20、Replan 10/20。
- Continue 有 11 success / 9 failure，使用标准化 Logistic Regression 结果模型。
- Retry 6/6 success、Replan 10/10 success，没有负类；使用 Beta(1,1) 平滑的 Bernoulli 常数模型，避免伪造失败样本或输出概率 1.0。
- 每个动作的 Token、Tool Calls、Latency 分量分别使用 Ridge 或常数回归器；保存的模型由非负包装器保证预测不小于 0。
- 特征复用 Day 10 的 26 个 Checkpoint 决策时刻特征，不读取分支结果或未来成本作为输入。
- 每个动作独立按 `template_id` 分组交叉验证；所有 fold 的训练/验证模板交集为空。
- 采用未调优的工程默认成本配置：`w_token=0.001`、`w_tool=1.0`、`w_latency=1.0`、`lambda=0.1`。该配置只用于初始离线验收，不表述为研究最优权重。
- 官方 Validation/Test 没有 Day 9 分支数据，因此没有重划 split 或使用 Test 调权重；评估口径为 Train-only template-grouped OOF。

## Remote Environment Work

- 无。Day 11 的数据构造、训练和离线评估全部在本地 WSL CPU 执行。
- 未连接远程服务器，未启动 SSH 隧道、vLLM 或 Qwen3-8B，未占用共享 GPU。

## Work Completed

- 新增 utility example schema，区分适用与不适用动作的标签语义。
- 新增 Day 9 utility 数据构造器、coverage audit 和来源 SHA-256。
- 实现三个独立结果模型以及每动作三个成本分量模型，共保存 12 个模型。
- 实现单类别动作的 Beta-Bernoulli 平滑 fallback，并在产物中记录 model kind。
- 实现非负成本模型包装器，OOF 和保存后模型的成本预测均不小于 0。
- 生成 36 条适用动作 OOF 预测以及 24 条显式空预测的不适用动作记录。
- 使用 OOF 概率和成本计算相对 Continue 的预测/实际净效用。
- 离线策略只在各 Checkpoint 的适用动作集合中选择动作，并与可观察动作中的 oracle 比较 regret。
- 两次相同配置运行产生相同 OOF utility predictions SHA-256：`b5469e2b253e1f7a5478d660cd1ad12de6b933239356d711679f07eafc12aa07`。
- 最终 training summary 的全部 Day 11 工程 Gate 为 true，`passed=true`。

## Files Changed

- `configs/day11_utility.yaml`
- `src/risk_agent/utility/__init__.py`
- `src/risk_agent/utility/schema.py`
- `src/risk_agent/utility/dataset.py`
- `src/risk_agent/utility/modeling.py`
- `scripts/train_day11_utility.py`
- `tests/test_day11_utility.py`
- `docs/daily_reports/day_11_report.md`

未修改 Day 7 split、Day 8/9 原始产物、动作语义、效用公式、风险标签、研究问题或本地/远程职责边界。

## Artifacts Generated

正式 run：`runs/day11/day11_20261119_082900_e8d013ce/`

- `utility_training_dataset.json`：60 条动作记录；适用动作有标签和成本，不适用动作对应字段为空。
- `coverage_audit.json`：动作覆盖、不适用原因、Split、特征来源和输入 SHA-256。
- `oof_utility_predictions.jsonl`：适用动作的 OOF 结果/成本/效用预测以及不适用动作的空预测。
- `outcome_metrics.json`：分动作 Outcome Brier 和 MAE。
- `cost_metrics.json`：分动作成本分量 MAE 和加权 Cost MAE。
- `utility_metrics.json`：跨动作 Outcome/Cost 汇总、Utility MAE 和 Policy Regret。
- `offline_policy_records.json`：20 个 Checkpoint 的适用动作、预测选择、oracle 和 regret。
- `training_summary.json`：coverage、指标、Gate 与产物路径。

最终模型：`models/utility/day11_20261119_082900_e8d013ce/`

- 3 个 action outcome joblib；
- 9 个 action × cost-component joblib；
- `model_metadata.json`。

非负模型包装完成前的预检 run `day11_20261119_082818_13b7484a` 及模型按只追加原则保留；最终验收采用 `day11_20261119_082900_e8d013ce`。joblib 二进制由 `.gitignore` 排除，元数据保留可审计。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q tests/test_day11_utility.py
.venv/bin/python scripts/train_day11_utility.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另运行只读模型加载 smoke，验证 3 个结果模型和 9 个成本模型可反序列化、结果概率位于 `[0,1]`、保存后的成本模型输出非负，以及两次 OOF predictions hash 一致。

## Test Results

- Day 11 专项测试：4 passed。
- 全量回归：61 passed。
- 依赖：`No broken requirements found.`
- Python 编译：通过。
- 输入：20 Checkpoint、60 action records、36 applicable、24 inapplicable。
- 不适用原因完整：无修正计划 10、无失败可重试 5、invalid argument 不可原样重试 5、stale observation 需先刷新 4。
- Outcome 模型：
  - Continue：20 样本，Brier 0.011966，MAE 0.087475；
  - Retry：6 样本，Brier 0.022865，MAE 0.150794；
  - Replan：10 样本，Brier 0.010000，MAE 0.100000；
  - 全部适用动作加权汇总：Brier 0.013236，MAE 0.101507。
- Cost 模型加权 MAE：
  - Continue 0.000272；
  - Retry 0.000710；
  - Replan 0.134645；
  - 全部适用动作汇总 0.037671。
- Utility MAE：全部适用动作 0.064500；非 Continue 动作 0.145126。
- Policy Regret：均值 0.005003，最大值 0.100047；4/20 Checkpoint 的 regret 大于 0。
- 离线选择：Continue 9、Retry 1、Replan 10。
- 每动作所有 fold 的 template overlap 均为空。
- 适用动作概率全部位于 `[0,1]`；OOF 与保存模型的成本预测均非负。
- 不适用动作 24/24 未生成概率、成本或效用预测。
- 官方 Validation/Test：未读取、未调权重、未报告伪造指标。
- 模型加载：12/12 joblib 成功。
- OOF 可复现性：两次运行 hash 完全一致。
- 敏感信息扫描：未发现 API Key、Authorization、密码或私钥内容。

## Acceptance Checklist

- [x] Continue 结果模型已训练。
- [x] Retry 结果模型已训练，并明确记录单类别 fallback。
- [x] Replan 结果模型已训练，并明确记录单类别 fallback。
- [x] 每个动作具有 Token、Tool Calls、Latency 成本模型。
- [x] 所有适用动作的成功概率位于 `[0,1]`。
- [x] OOF 和保存后的成本模型输出均非负。
- [x] 不适用动作未作为失败或零成本训练标签。
- [x] 每个动作按模板隔离交叉验证。
- [x] Test 未用于成本权重、lambda、特征或超参数选择。
- [x] 报告 Outcome Brier / MAE。
- [x] 报告 Cost MAE。
- [x] 报告 Utility MAE。
- [x] 报告 Policy Regret。
- [x] 保存动作 coverage 和不适用原因。
- [x] 未提前开发 Day 12 策略控制器。

## Known Issues

- 只有 36 条适用 Train 动作记录；官方 Validation/Test 尚无同状态分支，因此当前指标不是正式 Test 性能。
- Retry 和 Replan 的适用样本全部成功，缺少失败类别；其结果模型只是 Beta 平滑常数估计，不能学习状态条件差异。
- Day 9 使用本地确定性 reference controller，当前 utility 模型不能代表 Qwen3-8B 自主 Retry/Replan 的动作结果分布。
- 工具执行没有额外模型请求，因此所有 Token 成本为 0，Token 成本模型和 `w_token` 无法通过本批数据验证。
- 延迟仅为本地工具微秒/毫秒级耗时，不代表远程模型或真实 Docker 工作负载成本。
- `w_token=0.001`、`w_tool=1`、`w_latency=1`、`lambda=0.1` 是未调优的工程默认值。正式研究权重必须在足够的 Train/Validation 数据上选择，Test 只做最终评估。
- Policy Regret 仅在每个 Checkpoint 已观察且适用的动作集合中计算；它不是无偏因果策略价值估计，也不推断不适用动作的反事实结果。
- Utility MAE 的“全部适用动作”包含相对 Continue 恒为 0 的 Continue 项，因此同时报告更保守的非 Continue Utility MAE。

## Remaining Work

- Day 11 核心工程验收已通过。
- Day 12 集成时必须先应用动作安全/适用性 mask，再读取模型分数；模型异常应安全回退，且 Retry/Replan 需要次数上限。
- 正式策略结论前仍需采集包含失败 Retry/Replan、非零模型 Token 和官方 Validation/Test 的高质量同状态分支。
- 等待用户确认后才进入 Day 12。

## Status: PASS

三个动作的独立结果模型和成本模型已生成；概率、非负成本、动作适用性、模板隔离和全部要求指标均通过工程 Gate。PASS 表示 Day 11 初始离线效用估计链路完成，不表示当前小样本、本地 reference 数据已经支持真实模型泛化或因果策略结论。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
