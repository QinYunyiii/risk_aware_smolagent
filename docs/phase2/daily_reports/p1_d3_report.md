# P1-D3 Report

## Objectives

只执行 P1-D3：从 Day 9--13 历史产物独立复算动作覆盖，审计 Day 11 outcome/
cost 模型与 OOF，区分六类成本字段可观测性，并逐 Checkpoint 核验 Day 13 九策略
的实际动作、风险门控、效用排序、成本影响及消融代码路径。

未进入 P1-D4，未启动 Qwen3-8B、vLLM、SSH 隧道或 GPU 任务，未重新训练模型，
未修改 Recovery-Bench，也未修改 Day 7--14 历史产物。

## Local Environment Work

- 工作目录：`/home/three/projects/risk-aware-smolagents`
- Python：项目级 `.venv/bin/python`
- 审计模式：本地只读历史 JSON/JSONL/joblib；新派生产物写入
  `runs/phase2_audit/`
- 开始时 Git 工作树干净；现有提交 `a63d5e0` 保留为 P1-D2 基线

## Remote Environment Work

无。P1-D3 不需要网络、远程模型、GPU 或 SSH。

## Work Completed

1. 阅读 `AGENTS.md`、`PROJECT_GUIDE.md`、`NEXT_STAGE_EXPERIMENT_GUIDE.md`、
   P1-D1/P1-D2 审计与日报。
2. 从 Day 9 的 60 条原始候选分支复算动作适用性、成功/失败、原因、失败类别和
   成本字段。
3. 检查 Day 11 的 60 条训练数据、60 条 OOF、三类 outcome 模型、九个成本模型、
   fallback 条件和指标。
4. 检查 Day 12 的 20 条策略决策，并与 Day 13 Full 对齐。
5. 检查 Day 13 策略工厂和 180 条执行记录，生成 9×9 动作一致性矩阵和 20 条
   Checkpoint 差异表。
6. 实现确定性复算脚本和两项专项测试。

## Audit Results

### 动作覆盖

| 动作 | 候选 | Applicable | Not applicable | 成功 | 失败 |
|---|---:|---:|---:|---:|---:|
| CONTINUE | 20 | 20 | 0 | 11 | 9 |
| RETRY | 20 | 6 | 14 | 6 | 0 |
| REPLAN | 20 | 10 | 10 | 10 | 0 |

RETRY 仍为 6/6 全成功，REPLAN 仍为 10/10 全成功。24 条不适用记录在 Day 11
映射为 null，未错误进入训练失败标签。

### Day 11

- CONTINUE：20 条、11 正/9 负，Logistic Regression；但受 P1-D2 特征捷径影响。
- RETRY：6 条、6 正/0 负，Beta-Bernoulli 常数，最终概率 0.875。
- REPLAN：10 条、10 正/0 负，Beta-Bernoulli 常数，最终概率 0.916667。
- 三个 Token 成本模型均为常数 0；CONTINUE/RETRY Tool Calls 均为常数 1。
- OOF 文件存在且覆盖 60/60 候选记录；不适用记录保持 null。

判断：Day 11 证明工程训练/OOF 管线存在，但不能证明真实 Qwen 的状态条件结果或
完整干预成本具有预测能力。

### 成本可观测率

分母为 36 条 applicable 分支。

| 字段 | 可观测率 | 解释 |
|---|---:|---|
| Prompt Tokens | 0% | 无模型调用，且无分项字段 |
| Completion Tokens | 0% | 无模型调用，且无分项字段 |
| Tool Calls | 100% | 本地工具调用计数 |
| Model Latency | 0% | 当前执行模式不产生模型调用 |
| Tool Latency | 100% | 本地工具调用计时 |
| Total Wall-clock Latency | 0% | schema 缺失，不能用工具延迟替代 |

`extra_tokens=0` 是 local-reference runner 不调用模型并显式传 0，不代表真实干预
Token 成本低。

### Day 13

- Full 与 Risk + Utility：20/20 动作一致，20/20 完整 decision payload 一致；实际
  没有代码路径差异。
- risk_based、risk_plus_utility、full、without_risk、without_utility、
  without_cost 六策略：20/20 动作完全一致。
- without_risk：11 个风险 gate 从 low 改为 high，动作变化 0。
- without_utility：20 条 action score 均为空，动作变化 0。
- without_cost：20 个 Checkpoint 的 weighted cost 均变化，但排序变化 0、动作变化
  0；成本模型仍被调用，预测 component 仍保留。
- Day 12 与 Day 13 Full 的动作和风险 gate：20/20 一致。

判断：Day 13 的配对工程记录完整，但不能识别风险、效用和成本模块的策略贡献，也
不能支持真实 Qwen 或未见模板上的策略有效性。

## Files Changed

- `scripts/audit_p1_d3_utility_policy.py`
- `tests/test_p1_d3_utility_policy_audit.py`
- `docs/phase2/utility_policy_audit.md`
- `docs/phase2/daily_reports/p1_d3_report.md`

历史 Day 7--14 文件没有修改。

## Artifacts Generated

| 产物 | 内容 | SHA-256 |
|---|---|---|
| `runs/phase2_audit/action_coverage.json` | Day 9 覆盖、Day 11 模型、Day 13 等价性 | `41f5087d4353df3b7f7cf2f6205c9814f21031d8d6b3940e691e4c02ea16add7` |
| `runs/phase2_audit/policy_action_equivalence.csv` | 20 条逐 Checkpoint 九策略对照 | `c0b17a6661a1e599591883afc82a88188cbe2ac131d8387b35d77a964689fe8b` |
| `runs/phase2_audit/cost_observability.json` | 六类成本字段可观测性 | `815722d025627cd6c457f0795f1220649d74e596074ccfb83a0d228b81a28150` |

## Commands and Tests

```bash
.venv/bin/python scripts/audit_p1_d3_utility_policy.py
.venv/bin/python -m pytest -q tests/test_p1_d3_utility_policy_audit.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/audit_phase2_history.py --check-only
.venv/bin/python scripts/audit_p1_d3_utility_policy.py --check-only
.venv/bin/python -m py_compile scripts/audit_p1_d3_utility_policy.py
git diff --check
sha256sum runs/phase2_audit/action_coverage.json \
  runs/phase2_audit/policy_action_equivalence.csv \
  runs/phase2_audit/cost_observability.json
```

## Test Results

- P1-D3 专项测试：`2 passed in 0.76s`
- 全量回归：`84 passed in 8.62s`
- P1-D1 历史完整性：11/11 checks true，Gate PASS
- P1-D3 重复计算：输出逐字节一致，Gate PASS
- Python 编译：PASS
- `git diff --check`：PASS

## Acceptance Checklist

- [x] 从 Day 9 原始 JSONL 独立复算三动作覆盖和失败类别
- [x] 保留 not_applicable，未作为失败样本
- [x] 核验 RETRY 6/6、REPLAN 10/10
- [x] 核验 Day 11 训练样本、OOF、Beta fallback 和成本模型
- [x] 区分真实零、字段缺失、无模型调用和采集失败
- [x] 生成九策略动作一致性矩阵和逐 Checkpoint 差异清单
- [x] 证明 Full 与 Risk + Utility 实际完全等价
- [x] 核验三项消融的实际代码语义和决策影响
- [x] 专项测试、全量回归、历史完整性和重复计算通过
- [x] 未修改历史实验、未训练模型、未使用远程资源
- [x] 未开始 P1-D4

## Known Issues

1. RETRY/REPLAN outcome 仅有成功类，无法训练条件失败风险。
2. CONTINUE 模型仍受 P1-D2 特征捷径与合成数据问题约束。
3. Prompt/Completion Token、模型延迟和总 wall-clock 不可观测。
4. Full 与 Risk + Utility 重复，六个模型相关策略动作完全等价。
5. without_cost 只移除成本对决策的影响，没有停止成本模型计算。
6. 全部 Day 13 记录来自 Train/local-reference，不能做泛化结论。

## Remaining Work

P1-D3 无剩余核心审计工作。上述问题需在后续阶段重新设计真实数据采集和实验协议；
按照用户边界，本次不提出或实施 P1-D4 改动。

## Status: PASS

该 PASS 只表示 P1-D3 审计 Gate 完成且可复算，不表示 Day 11 模型有效或 Day 13
策略研究假设成立。

## Git Commit

本次 P1-D3 修改尚未创建 Git commit，保留等待人工审核。
