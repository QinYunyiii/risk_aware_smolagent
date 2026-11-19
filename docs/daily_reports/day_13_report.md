# Day 13 Report

## Objectives

- 在固定的 20 个 Day 9 Checkpoint 上比较 5 个基线：Vanilla Agent、Always Retry（仅安全适用）、Rule-based、Risk-based、Risk + Utility。
- 完成 4 个消融：Full、w/o Risk、w/o Utility、w/o Cost。
- 每个策略从独立 Checkpoint 副本恢复并真实执行所选本地工具动作。
- 报告成功率、恢复率、干预率、不必要干预率、Token、Latency、Tool Calls 和 Policy Regret，并按领域与故障类型分层。
- 进行同 Checkpoint 配对比较，保留安全/适用性、预算和 Split 约束。
- 只完成 Day 13，不提前执行 Day 14 Recovery-Bench 扩展验证与交付工作。

## Local Environment Work

- 复用 Day 9 正式 run `runs/day09/day9_20261119_080843_288841fc` 的 20 个 Train Checkpoint 和可适用动作观测结果。
- 加载 Day 10 LightGBM 风险模型 `models/risk/day10_20261119_082058_aab1d057`。
- 加载 Day 11 outcome/cost 模型 `models/utility/day11_20261119_082900_e8d013ce`。
- 固定使用 Day 12 的 Train-only 工程配置：风险阈值 0.5、最小效用增益 0.05、Retry/Replan 最大次数各 1、相同干预动作连续上限 1。
- 所有策略都先应用动作适用性、安全性和预算约束；Always Retry 不会重试不安全或不适用的动作。
- 每个策略/Checkpoint 组合使用唯一 `branch_id`、独立决策恢复目录和独立执行工作区。
- 本日没有修改风险标签、动作语义、效用/成本公式、数据划分或本地/远程职责边界。

## Remote Environment Work

- 无。Day 13 的策略推断、Checkpoint Restore、工具执行、验证和指标计算全部在本地 WSL 完成。
- 未连接远程服务器，未启动 SSH 隧道、vLLM 或 Qwen3-8B，未占用实验室 GPU。

## Work Completed

- 新增 Day 13 基线/消融策略工厂：
  - `vanilla`：始终 Continue；
  - `always_retry`：Retry 仅在安全且适用时执行，否则安全回退 Continue/Stop；
  - `rule_based`：F1/F2 → Retry，F3–F6 → Replan，无故障 → Continue；
  - `risk_based`：低风险 Continue，高风险按安全优先级 Replan → Retry → Continue；
  - `risk_plus_utility` / `full`：Day 12 风险 + 效用 + 成本 + 安全/预算策略；
  - `without_risk`：固定为高风险，只保留效用、成本和安全约束；
  - `without_utility`：仅风险门控和安全动作优先级；
  - `without_cost`：保留风险与结果概率，将加权成本置零。
- 扩展 `InterventionBranchRunner`，允许策略级 `branch_id` 前缀，避免多个策略执行相同 Checkpoint/Action 时 ID 冲突。
- 建立 Day 13 可复现实验脚本，支持小规模 smoke 和完整 9 × 20 正式运行。
- 生成 180 条策略任务记录和 180 条所选分支结果；共执行 181 次本地工具调用。Rule-based 在一个 F6 Replan 分支执行了两步，其余每个分支一次。
- 生成整体、领域、故障类型指标；所有分层均包含所要求的可靠性、干预、成本和 Regret 字段及成本/Regret 标准差。
- 完成 Full 与所有基线/消融的同 Checkpoint 配对差值。
- 最终正式 summary 的 15 项完整性、安全性和实验 Gate 全部为 true，`passed=true`。

## Metric Definitions

- Task Success Rate：确定性验证成功任务数 / 该策略任务数。
- Recovery Rate：所选动作成功数 / 源 Day 9 Continue 失败数；分母为 0 的分层在 JSON 中序列化为 0.0，解释时记为 N/A。
- Intervention Rate：非 Continue 决策数 / 该策略任务数。
- Unnecessary Intervention Rate：在源 Continue 本可成功的任务上发生的干预数 / 全部干预数；无干预时序列化为 0.0。
- Token、Latency、Tool Calls：所选分支的实测增量，按任务报告总体均值和总体标准差。
- Policy Regret：同一 Checkpoint 上，Day 9 所有安全适用动作的最大观测价值减去策略所选动作的观测价值；观测价值为成功指示减去加权实测成本。本指标使用 Day 9 配对分支，不是 Day 11 模型预测值。

## Main Results

最终正式 run：`runs/day13/day13_20261119_085002_5754c690/`。

| Policy | Success | Recovery | Intervention | Unnecessary / Intervention | Tokens mean±sd | Tools mean±sd | Latency ms mean±sd | Regret mean±sd |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Vanilla | 55% | 0% | 0% | 0% | 0±0 | 1.00±0.00 | 0.229±0.446 | 0.45000181±0.49749374 |
| Always Retry | 55% | 0% | 30% | 100% | 0±0 | 1.00±0.00 | 0.268±0.606 | 0.45000084±0.49749461 |
| Rule-based | 100% | 100% | 75% | 40% | 0±0 | 1.05±0.22 | 0.303±0.594 | 0.00500237±0.02180475 |
| Risk-based | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.265±0.490 | 0.00000099±0.00000304 |
| Risk + Utility | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.297±0.502 | 0.00000099±0.00000304 |
| Full | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.288±0.493 | 0.00000099±0.00000304 |
| w/o Risk | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.271±0.519 | 0.00000099±0.00000304 |
| w/o Utility | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.283±0.541 | 0.00000099±0.00000304 |
| w/o Cost | 100% | 100% | 45% | 0% | 0±0 | 1.00±0.00 | 0.313±0.512 | 0.00000099±0.00000304 |

Full 相对 Vanilla 的配对成功率差为 +45 个百分点、恢复率差为 +100 个百分点、平均 Regret 差为 -0.45000083。Full 与 Rule-based 成功率相同，但干预率低 30 个百分点、平均工具调用少 0.05、平均 Regret 低 0.00500138。Full 与 Risk-based、Risk + Utility 及三个消融在离散动作、成功、干预、工具次数和 Regret 上完全相同；仅微秒级运行时噪声不同。

### Full by Domain

| Domain | n | Success | Recovery | Intervention | Unnecessary / Intervention | Tools mean±sd | Latency ms mean±sd | Regret mean±sd |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| API workflow | 4 | 100% | 100% | 25.0% | 0% | 1.00±0.00 | 0.824±0.877 | 0.00000014±0.00000025 |
| Calculation logic | 5 | 100% | 100% | 40.0% | 0% | 1.00±0.00 | 0.021±0.008 | 0.00000016±0.00000031 |
| Data analysis | 5 | 100% | 100% | 40.0% | 0% | 1.00±0.00 | 0.133±0.005 | 0.00000090±0.00000115 |
| File retrieval | 6 | 100% | 100% | 66.7% | 0% | 1.00±0.00 | 0.283±0.160 | 0.00000232±0.00000518 |

所有领域的 Token 均为 0±0。完整 9 个策略的领域分层见 `experiment_metrics.json`。

### Full by Fault Type

| Fault | n | Success | Recovery | Intervention | Unnecessary / Intervention | Tools mean±sd | Latency ms mean±sd | Regret mean±sd |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| F0 No Fault | 5 | 100% | N/A | 0% | 0% | 1.00±0.00 | 0.160±0.183 | 0±0 |
| F2 API 503 | 5 | 100% | N/A | 0% | 0% | 1.00±0.00 | 0.296±0.262 | 0.00000383±0.00000512 |
| F3 Invalid Argument | 5 | 100% | 100% | 100% | 0% | 1.00±0.00 | 0.144±0.084 | 0±0 |
| F4 Stale Observation | 4 | 100% | 100% | 100% | 0% | 1.00±0.00 | 0.121±0.073 | 0±0 |
| F6 Partial Failure | 1 | 100% | N/A | 0% | 0% | 1.00±0.00 | 2.286±0.000 | 0.00000057±0 |

所有故障类型的 Token 均为 0±0。F0、F2、F6 的源 Continue 在本批任务中均成功，因此没有 Recovery Rate 分母。完整 9 个策略的故障分层见 `experiment_metrics.json`。

## Files Changed

- `configs/day13_experiment.yaml`
- `src/risk_agent/evaluation/__init__.py`
- `src/risk_agent/evaluation/policies.py`
- `src/risk_agent/intervention/runner.py`
- `scripts/run_day13_experiment.py`
- `tests/test_day13_policies.py`
- `docs/daily_reports/day_13_report.md`

未修改 Day 7 split、Day 8–12 原始产物、Recovery-Bench 原仓库或远程部署。

## Artifacts Generated

最终正式 run：`runs/day13/day13_20261119_085002_5754c690/`

- `policy_task_results.jsonl`：180 条策略决策、执行结果、源 Continue 状态和 Regret。
- `selected_branch_outcomes.jsonl`：180 条真实本地分支执行与确定性验证结果。
- `experiment_metrics.json`：9 个策略的总体、领域和故障类型完整指标。
- `paired_comparisons.json`：Full 与 5 个基线、3 个非 Full 消融的同 Checkpoint 差值。
- `main_experiment_summary.json`：配置来源、数据范围、15 项 Gate 和最终状态。
- `decision_states/`：180 个策略决策前的独立 Restore 副本。
- `policy_branches/`：180 个所选动作的独立执行环境和轨迹。

只追加保留的预检/旧 run：

- `day13_smoke_20261119_084616_0f673bfa`：首次 smoke 在生成配对汇总时失败；原因是 smoke 仅含 Vanilla/Full，而旧条件错误要求全部策略。部分产物保留，未用于验收。
- `day13_smoke_20261119_084627_f0f33e8e`：修复条件后的首个成功 smoke。
- `day13_20261119_084631_2e3ff509`：首个完整正式 run；核心结果通过，但领域/故障分层缺少部分要求字段，故被后续 run 取代。
- `day13_smoke_20261119_084955_d6d68808`：补齐分层指标后的最终 smoke，4/4 记录通过全部 Gate。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_day13_policies.py tests/test_day12_policy.py tests/test_day9_interventions.py
.venv/bin/python scripts/run_day13_experiment.py --smoke
.venv/bin/python scripts/run_day13_experiment.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另使用只读审计核对 180 条记录的策略/Checkpoint 唯一性、Branch ID、工作区、Split、动作适用性、安全排除项、Restore hash、controller、工具计数和分层指标 schema；执行私钥和常见 Bearer/API Key 字面量扫描。

## Test Results

- Day 13 + Day 12 + Day 9 专项回归：14 passed。
- 全量回归：71 passed。
- 最终正式实验：9 个策略 × 20 个 Checkpoint = 180 条任务记录。
- 正式 Gate：15/15 true；`passed=true`。
- 策略/Checkpoint 对：180/180 唯一。
- Branch ID：180/180 唯一；执行工作区：180/180 唯一。
- Restore hash：180/180 匹配。
- 所选动作：180/180 安全适用，180/180 未出现在排除动作中。
- Split：180/180 为 Train；未访问 Validation/Test。
- 工具执行：181 次，均在本地 WSL；额外模型 Token 为 0。
- 本地工具总实测延迟约 0.05036 秒。
- 远程/API 基础设施失败：0；策略 fallback：0；Stop：0。
- 依赖检查：`No broken requirements found.`。
- Python 编译：通过。
- 敏感信息扫描：未发现私钥内容或常见字面量 Bearer/API Key。

## Acceptance Checklist

- [x] 完成 Vanilla Agent 基线。
- [x] 完成仅安全适用的 Always Retry 基线。
- [x] 完成 Rule-based 基线。
- [x] 完成 Risk-based 基线。
- [x] 完成 Risk + Utility 基线。
- [x] 完成 Full、w/o Risk、w/o Utility、w/o Cost 四个消融。
- [x] 每个策略使用相同 20 个 Checkpoint 并独立 Restore。
- [x] 所有动作通过安全性、适用性和预算约束。
- [x] 报告成功率、恢复率、干预率和不必要干预率。
- [x] 报告 Token、Latency、Tool Calls 和 Regret 的均值与标准差。
- [x] 按领域和故障类型完成全部要求指标分层。
- [x] 完成同 Checkpoint 配对比较。
- [x] 未使用 Test 调阈值、权重或超参数。
- [x] 未提前执行 Day 14。

## Known Issues

- 20 个 Checkpoint 全部属于 Train，并且是 Day 10/11 模型的同源数据。本日结果是初始工程主实验，不是独立 Validation/Test 泛化结果。
- 样本量只有 20；领域分层为 4–6 个任务，F6 只有 1 个任务，均不足以支持稳健统计推断或置信区间结论。
- Risk-based、Risk + Utility、Full 和三个消融在本批数据上产生完全相同的动作。该结果说明当前故障模式过于可分，不能证明各组件没有贡献，也不能证明 Full 优于这些方法。
- Risk + Utility 基线与 Full 当前使用相同实现；Full 名称用于消融参考。只有后续增加不同组件或协议后，两者才可能产生差异。
- Always Retry 的 6 次干预都发生在源 Continue 可成功的任务上，因此没有恢复失败任务；这反映本项目的安全适用性和一次性故障语义，不代表 Retry 在一般任务上无效。
- Regret 使用 Day 9 已观测配对分支的成功和成本；Day 13 执行延迟只用于本日成本报告，不回填 Regret，以保持所有策略使用同一 oracle 比较基准。
- 所有动作由本地 reference 控制器执行，没有远程 Qwen3-8B 推理，因此 Token 为 0，延迟仅代表本地轻量工具，不是完整 Agent 端到端成本。
- 总体标准差为总体标准差（population standard deviation）；当前未报告置信区间或显著性检验。
- 仓库当前文件仍全部未跟踪；未创建 Git commit，历史保护依赖只追加产物和不覆盖已有文件。

## Remaining Work

- Day 13 核心工程验收已完成。
- 在解释研究结果前，需要独立 Validation/Test、更多模板/故障组合、真实模型轨迹和更大样本；这些不是本次 Day 13 PASS 的组成部分。
- Day 14 若进入 Recovery-Bench 扩展验证，必须先再次审计实际一致状态恢复能力；不支持时只能报告限制，不能把普通失败日志当作同状态分支效用数据。
- 等待用户确认后才进入 Day 14。

## Status: PASS

5 个基线与 4 个消融均在相同 20 个 Train Checkpoint 上完成独立 Restore、真实本地工具执行、确定性验证、完整分层指标和配对比较；15 项实验 Gate 全部通过。PASS 仅表示 Day 13 工程验收通过，不表示已有正式泛化证据或统计显著性结论。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件和所有历史实验产物均保留，未删除、覆盖或重建。
