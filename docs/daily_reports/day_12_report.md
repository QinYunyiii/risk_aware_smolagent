# Day 12 Report

## Objectives

- 集成 Day 10 风险模型与 Day 11 outcome/cost 模型。
- 在任何模型评分前应用动作安全性、适用性和预算约束。
- 低风险状态直接 Continue；高风险状态只比较安全适用且预算可承受的动作。
- 只有相对 Continue 的净效用超过门槛时才干预。
- 为 Retry/Replan 设置次数和连续循环上限。
- 模型异常时执行安全回退。
- 对至少 20 个真实 Checkpoint 完成本地端到端决策、Restore、动作执行和确定性验证。
- 只完成 Day 12，不提前实施 Day 13 基线、消融或主实验。

## Local Environment Work

- 加载 Day 10 正式 LightGBM 风险模型和 Day 11 的 3 个结果模型、9 个成本模型。
- 同时校验 risk/utility metadata 的 catalog SHA-256 和 26 维特征 schema。
- 复用 Day 9 正式 run 的 20 个 Train Checkpoint；每次策略决策前恢复并核对状态 hash，动作执行再从原 Checkpoint 独立 Restore。
- 固定策略顺序：预算检查 → 动作安全/适用性 mask → 风险预测 → 低风险 Continue → 高风险 utility 比较 → 收益门槛 → Continue/Stop。
- 工程默认阈值：低风险阈值 0.5，最小相对净效用增益 0.05；仅使用 Train 数据制定，未访问 Test。
- Retry 和 Replan 最大次数均为 1，相同干预动作最大连续 streak 为 1。
- 预算检查覆盖剩余 Token、Tool Calls、Latency；Tool Calls 使用向上取整并至少预留 1 次调用。
- 模型异常回退：只读且有最低预算时 Continue；写操作或无安全 Continue 时 Stop。
- 决策后结构化记录风险、动作分数、成本、相对效用、排除动作和原因、预算及 fallback 类型。

## Remote Environment Work

- 无。Day 12 的模型推断、策略控制、Checkpoint Restore、工具执行和验证全部在本地 WSL。
- 未连接远程服务器，未启动 SSH 隧道、vLLM 或 Qwen3-8B，未占用共享 GPU。

## Work Completed

- 新增 `PolicyModelBundle`，安全加载风险、结果和成本模型，并验证概率、成本和特征。
- 新增 `AdaptiveInterventionPolicy`，实现风险门控、效用比较、安全 mask、预算、循环限制和模型异常回退。
- 新增结构化 `ActionScore` 与 `PolicyDecision`。
- 为 Day 9 `PreparedCheckpoint` / `InterventionScenario` 增加安全反序列化。
- 为 `InterventionBranchRunner` 增加单动作公开入口和可追踪 controller/model 版本，不改变 Day 9 默认行为。
- 在 20 个 Checkpoint 上完成自适应策略端到端执行：
  - 11 个低风险状态选择 Continue；
  - 9 个高风险 invalid/stale 状态比较动作后选择 Replan；
  - 20/20 动作成功并通过确定性验证；
  - 20/20 初始/恢复 hash 一致；
  - 20 个工作区互相独立。
- 运行 8 项显式安全探针：unsafe Retry、首次安全 Retry、Retry 循环、首次安全 Replan、Replan 循环、只读模型异常、写操作模型异常、预算耗尽。
- 最终 summary 的全部 Day 12 Gate 为 true，`passed=true`。

## Files Changed

- `configs/day12_policy.yaml`
- `src/risk_agent/controller/__init__.py`
- `src/risk_agent/controller/schema.py`
- `src/risk_agent/controller/models.py`
- `src/risk_agent/controller/policy.py`
- `src/risk_agent/intervention/schema.py`
- `src/risk_agent/intervention/runner.py`
- `scripts/run_day12_policy.py`
- `tests/test_day12_policy.py`
- `docs/daily_reports/day_12_report.md`

未修改 Day 7 split、Day 8–11 原始产物、风险标签、动作语义、效用/成本公式、研究问题或本地/远程职责边界。

## Artifacts Generated

正式 run：`runs/day12/day12_20261119_083943_b18d954e/`

- `policy_decisions.jsonl`：20 条风险、utility、安全 mask、预算和动作决策。
- `selected_branch_outcomes.jsonl`：20 条所选动作的真实本地执行与验证结果。
- `policy_e2e_records.json`：决策与执行结果的一一对应记录。
- `safety_probes.json`：8 项安全/异常/循环/预算探针及完整决策。
- `policy_summary.json`：动作分布、成功数、策略配置和全部验收 Gate。
- `decision_states/`：策略输入的独立 Restore 副本。
- `selected_branches/`：实际所选动作的独立 Restore 环境与工具轨迹。

补充 Replan 循环探针前的预检 run `day12_20261119_083904_823c2578` 也按只追加原则保留；最终验收采用 `day12_20261119_083943_b18d954e`。

## Commands and Tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_day12_policy.py tests/test_day9_interventions.py
.venv/bin/python scripts/run_day12_policy.py
.venv/bin/python -m pytest -q tests/test_day12_policy.py
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src scripts tests
```

另使用只读审计复核 scenario/action/risk 分布、排除原因、分支 controller、成功率、Restore hash、独立工作区和实测成本。

## Test Results

- Day 12 专项测试：6 passed。
- Day 12 + Day 9 兼容性测试：9 passed。
- 全量回归：67 passed。
- 依赖：`No broken requirements found.`
- Python 编译：通过。
- 端到端任务：20；成功 20；确定性验证通过 20。
- 风险分布：low 11、high 9。
- 动作分布：Continue 11、Replan 9、Retry 0、Stop 0。
- 场景/动作：
  - transient service unavailable → Continue 5；
  - no fault pending → Continue 5；
  - partial failure not committed → Continue 1；
  - invalid argument → Replan 5；
  - stale observation → Replan 4。
- 低风险：11/11 Continue。
- 高风险：9/9 至少比较 Continue 和 Replan，9/9 选择超过门槛的 Replan。
- 干预率：9/20 = 45%。
- 正常端到端 fallback：0；Stop：0。
- 所选动作安全性：20/20 未出现在 excluded actions。
- Restore hash：20/20 匹配；工作区：20/20 唯一。
- 本地工具调用：20；额外模型 Token：0。
- 工具延迟：总计约 0.005057 秒，均值约 0.000253 秒。
- 安全探针：8/8 通过。
  - unsafe Retry 被阻止；
  - 首次安全 Retry 可选，第二次被次数/循环限制阻止；
  - 首次安全 Replan 可选，第二次被次数/循环限制阻止；
  - 风险模型异常时只读操作回退 Continue；
  - 风险模型异常时写操作回退 Stop；
  - 预算耗尽在模型调用前 Stop。
- 敏感信息扫描：未发现 API Key、Authorization、密码或私钥内容。

## Acceptance Checklist

- [x] 集成风险模型。
- [x] 集成三个动作的结果和成本模型。
- [x] 策略先检查安全约束和预算。
- [x] 低风险优先 Continue。
- [x] 高风险比较安全适用动作的净效用。
- [x] 不安全或不适用 Retry 被排除。
- [x] 净收益不足时回退 Continue。
- [x] 无安全动作、预算耗尽或不安全模型异常时 Stop。
- [x] Retry 具有次数和连续循环上限。
- [x] Replan 具有次数和连续循环上限。
- [x] 模型异常有明确安全回退。
- [x] 至少 20 个端到端任务完成；实际 20/20 成功。
- [x] 每次执行从独立 Checkpoint 副本恢复。
- [x] 决策和工具执行均结构化记录。
- [x] 未提前执行 Day 13。

## Known Issues

- 20 个端到端任务与 Day 10/11 训练来源属于同一批 Train Checkpoint；本结果验证工程集成，不是独立泛化评估。
- 风险模型在当前场景上只产生约 0.0565 和 0.9435 两档概率，主要学习了人工故障场景规律。
- Retry/Replan outcome 模型缺少失败类别；Day 12 的 utility 比较仍继承 Day 11 的单类别 Beta 常数模型限制。
- 正常端到端策略没有选择 Retry；Retry 的安全选择与循环限制通过显式探针验证，不能替代真实多轮 Retry 轨迹。
- 每个端到端任务只做一个策略决策和一个所选动作；多轮在线循环由两次决策探针验证上限，但尚未进行真实长链路 Agent 循环实验。
- 阈值 0.5、收益门槛 0.05 和 Day 11 成本权重都是 Train-only 工程默认值，未在官方 Validation 上调优。
- 本批工具执行不调用远程模型，Token 成本为 0，延迟仅代表本地轻量工具。
- 20/20 成功不代表真实 Qwen3-8B、官方 Test、Recovery-Bench 或复杂 Docker 任务同样成功。

## Remaining Work

- Day 12 核心工程验收已通过。
- Day 13 开始前必须保持本日阈值与成本配置可追踪；基线/消融不得使用 Test 调参。
- Day 13 的主实验需要明确区分本地 reference Train 集成结果与真实模型/独立 split 结果，并报告动作覆盖和数据限制。
- 等待用户确认后才进入 Day 13。

## Status: PASS

风险、效用、安全约束、预算与循环限制已集成；20 个 Checkpoint 的策略决策和所选动作全部真实 Restore、执行并通过确定性验证，8 项安全探针全部通过。PASS 表示 Day 12 初始工程链路满足指南验收，不表示当前同源 Train 数据已经支持正式策略泛化结论。

## Git Commit

未提交；用户未要求创建提交。仓库现有未跟踪文件均保留，未覆盖或删除用户数据。
