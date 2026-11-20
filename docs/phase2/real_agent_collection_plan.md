# 真实 Qwen 风险数据采集计划

> 文档状态：B0 采集草案，等待 B1 Protocol 确认\
> 日期：2026-11-19（Asia/Shanghai）\
> 正式采集状态：未开始\
> 当前合格正式风险样本：0

## 1. 目标

建立以下可审计闭环：

```text
真实 Qwen 任务执行
  → Agent-visible Tool Observation
  → 完整决策 Checkpoint
  → 独立 Restore
  → 无干预 Qwen CONTINUE
  → Deterministic Verifier
  → Y_continue
  → 决策时刻无泄漏风险特征
```

第一版只要求风险标签所需的 CONTINUE 闭环。RETRY/REPLAN 在该闭环通过后另行扩展。

## 2. 数据来源分层

每条记录必须声明：

| 字段 | 允许值 | 含义 |
|---|---|---|
| `controller_type` | `qwen3_model` / `deterministic_local_reference` | 谁决定下一步 |
| `execution_mode` | `model_backed_smoke` / `model_backed_formal` / `reference` | 执行模式 |
| `formal_experiment` | boolean | 是否可进入正式数据集 |
| `checkpoint_schema_version` | `1` / `2` | 只有完整 v2 可进入正式数据 |

历史数据处理：

- Day 8：保留为真实 Qwen 轨迹基线，不转为 v2 Checkpoint；
- Day 9–13：保留为 reference 工程对照，不进入真实 Qwen 风险训练；
- Day 10：保留为捷径审计对象，不进入新风险数据；
- Day 14：保留为只读 Adapter 证据，不进入 MiniToolBench-R Checkpoint 数据。

## 3. 阶段计划

### B1：冻结 Protocol 与 Schema

必须确认：

- 决策时刻；
- tri-state 标签；
- max-step/budget stop 规则；
- Agent-visible fault response；
- 内部故障信息隔离；
- feature allowlist；
- 模板和 split；
- Checkpoint schema v2；
- 最大 step/token/time；
- 数据规模和停止条件。

未经确认不修改研究语义。

### B2：实现完整 Agent Checkpoint

- public stream 捕获 ActionStep；
- 保存 messages/full steps/Agent runtime；
- 扩展 Checkpoint schema v2；
- Restore 后下一轮输入 exact-match；
- 保持 v1 兼容；
- 先用 fake model 和本地工具测试；
- 再用至少 5 个真实 Qwen 任务验收。

### B3：实现模型驱动 CONTINUE

- 每个分支独立 Restore；
- 使用同一 Worker、模型、Prompt、工具和 generation config；
- 不增加干预 Prompt；
- 记录模型/工具/总延迟和 Token；
- 由 outcome evaluator + verifier 产生标签。

### B4：model-backed smoke

建议：

- 8–12 个 Train/开发任务；
- 四领域；
- 至少四类故障；
- 至少 5 个有效 Checkpoint；
- 所有数据标记 `formal_experiment=false`；
- smoke 数据不并入正式训练集。

### B5：Train 正式采集

现有指南建议：

- 48 个 Train 模板；
- 每模板 2 个实例；
- 计划 96 个任务；
- 最低 72 个有效 Checkpoint，目标 96。

这些数字须经 B1 确认后冻结。

### B6：Validation 正式采集

- 16 个 Validation 模板；
- 每模板 2 个实例；
- 最低 24，目标 32 个有效 Checkpoint；
- 用于模型选择、校准和阈值；
- 不能据结果修改模板或故障分布。

### B7：Test 正式采集和锁定

- 16 个 Test 模板；
- 每模板 2 个实例；
- 最低 24，目标 32 个有效 Checkpoint；
- 采集前冻结 Protocol、Prompt、模型、工具、Verifier 和 allowlist；
- 标签独立目录保存；
- 只允许完整性/hash/split 检查。

### B8：质量审计与冻结

最低建议：

- 总有效真实 Checkpoint ≥120；
- Train/Validation/Test ≥72/24/24；
- Restore hash match = 100%；
- 模型成本关键字段可观测率 ≥95%；
- Continue 有成功和失败两类；
- 所有风险特征有决策时刻证据；
- Test 隔离守卫通过。

## 4. 任务与模板计划

当前 Day 7 catalog 的 200 个实例全部为单 reference-step。它可以用于：

- 兼容性测试；
- 基础捕获测试；
- 小规模 smoke；
- 验证旧 Verifier/TaskEnvironment 不回归。

若沿用现有 Protocol v2 建议，需新增 40 个真正多步骤模板：

- 每领域 10；
- 每领域 6 Train / 2 Validation / 2 Test；
- 每个模板 3–8 个有依赖关系的工具步骤；
- 不通过拆分单步或增加文字长度伪造复杂度；
- 每个模板有确定性正向与负向 verifier probe。

新增模板、split 和样本数属于研究设计，必须在 B1 获得用户确认。

## 5. 决策 Checkpoint 选择

每个正式任务优先采集一个预注册时刻，防止按结果挑选：

1. 首个符合捕获条件的非 final Tool Response；或
2. fault schedule 指定 step 的 Tool Response；或
3. 预注册的正常中间 step。

选择规则必须只依赖决策时刻信息，不能查看最终 outcome。

若一个任务采集多个 Checkpoint：

- 使用不同 `checkpoint_id`；
- 保留同一 `task_run_id`；
- 训练/统计按 task/template 聚类；
- 不把它们当作完全独立样本。

## 6. Task Attempt 状态

建议状态：

```text
checkpoint_captured
success
task_failure
agent_failure
tool_failure_observed
model_service_infra_failure
environment_infra_failure
operator_cancelled
budget_terminated
```

source run 可以在成功捕获后停止，以节省 Token；此时状态必须为
`checkpoint_captured`，不能伪写 success。风险标签只来自独立 Continue branch。

是否让 source run 在捕获后继续完成，由 B1 冻结；两种方案不得在同一正式 run 中混用。

## 7. CONTINUE outcome 和风险标签

### 7.1 Continue branch

- `action=CONTINUE`；
- `applicable=true`；
- `controller_type=qwen3_model`；
- `execution_mode=model_backed_formal`；
- 不增加新提示；
- 使用剩余预算；
- same Checkpoint 重复运行需不同 attempt ID。

### 7.2 标签

```text
Y_continue = 1:
  normal completion AND verifier_passed

Y_continue = 0:
  observable task/agent failure OR verifier_failed

Y_continue = null:
  model service failure, unexpected environment failure,
  or otherwise censored outcome
```

风险训练样本只从非 null outcome 派生。null 记录继续保留在质量审计中。

## 8. 无泄漏特征生成

### 8.1 允许来源

- canonical Agent messages；
- Tool Call 历史；
- Agent-visible Observation/error；
- 已发生的 model/tool latency；
- 已使用 budget；
- 当前 step/progress；
- Agent 当时可见的任务/domain 信息。

### 8.2 禁止来源

- `fault_type`、fault ID、schedule、seed；
- internal commit truth；
- expected answer；
- verifier hidden checks/result；
- Continue outcome；
- 后续工具调用和未来成本；
- Retry/Replan 分支结果；
- Day 9 `last_failure` synthetic metadata。

### 8.3 特征生成顺序

```text
Checkpoint captured
  → canonical observation/messages frozen
  → features generated and hashed
  → Continue branch executed
  → outcome generated
  → lineage join into risk_example
```

正式训练器只读取 `feature_allowlist.json` 中明确列出的字段。

## 9. 原始产物布局

```text
runs/phase2_<split>/<run_id>/
├── run_manifest.json
├── config_snapshot.yaml
├── task_attempts.jsonl
├── model_events.jsonl
├── tool_events.jsonl
├── agent_visible_events.jsonl
├── decision_checkpoints.jsonl
├── continue_outcomes.jsonl
├── verifier_outcomes.jsonl
├── checkpoints/<checkpoint_id>/**
├── branches/<branch_id>/**
├── artifact_manifest.json
├── resource_audit.json
└── collection_summary.json
```

原始 JSONL append-only；派生文件不得反向覆盖原始消息或 Checkpoint。

## 10. ID 与 lineage

必须唯一并可关联：

```text
experiment_id
run_id
task_run_id
model_request_id
tool_event_id
checkpoint_id
branch_id
branch_attempt_id
verifier_outcome_id
risk_example_id
```

一个风险样本的 lineage：

```text
risk_example_id
  ├── checkpoint_id
  │     ├── source task_run_id
  │     ├── Agent state artifacts
  │     └── feature cutoff
  └── continue branch_id
        ├── model/tool events
        └── verifier outcome
```

## 11. 分割与版本控制

- Split 由 `template_id` 决定；
- 同模板全部实例和 Checkpoint属于一个 split；
- 同 Checkpoint 全部分支属于同一 split；
- catalog、split、Prompt、Tool、Verifier、feature schema 均保存 hash；
- 实现 Bug 需要新 protocol/config version；
- 不覆盖受影响旧 run；
- Test 训练守卫默认拒绝读取 Test label。

## 12. 失败和续跑策略

### 12.1 允许安全续跑

- 模型请求前失败；
- 没有工具调用和环境副作用；
- 明确确认 branch workspace 未变化；
- 使用同一 logical task、不同 attempt ID；
- 保留原失败 attempt。

### 12.2 禁止自动重放

- 工具写操作已发生；
- commit status 为 committed/unknown；
- Checkpoint snapshot 完成状态不确定；
- Restore hash 不匹配；
- 模型完成 Tool Call 后传输中断且副作用不明。

发生上述情况时 outcome 记 null/infra 或进入人工审计，不得重跑到成功。

## 13. Worker、TLS 与 GPU 复用

复用：

- `configs/remote_model.yaml`；
- `RemoteModelSettings`；
- `OpenAIServerModel`；
- 严格 CA 校验；
- API Key 环境变量；
- `scripts/manage_remote_vllm.py` 的所有权和 tunnel 逻辑。

正式启动前增加：

- 实验室级资源确认；
- 两次 GPU 采样；
- 明确物理 GPU ID/UUID；
- Worker 映射和检查时间记录；
- run-specific remote/local state；
- 8K context preflight；
- 磁盘、端口、Worker、隧道和释放记录。

不得仅凭当前容器 `nvidia-smi` process 表为空判断 GPU 空闲。

## 14. 资源预算

### 14.1 历史测量基线

Day 8 正式 run：

- 20 个 token-complete attempt；
- 70,333 total tokens；
- 平均 3,516.7 tokens；
- 平均 latency 10.86 秒；
- 数据约 1.1 MB；
- 主要为单步任务。

### 14.2 建议预算

| 阶段 | GPU | Token | 磁盘 | 预计时间 |
|---|---|---:|---:|---:|
| B2 本地实现 | 0 | 0 | <0.5 GB | 2–4 天 |
| B2/B3 5-task Gate | 最多 2×RTX 4090 | 0.1–0.3M | <0.5 GB | 1–3 小时服务时间 |
| B4 8–12 task smoke | 最多 2×RTX 4090 | 0.2–0.8M | 1–2 GB | 2–4 小时 |
| 160 task formal | 最多 2×RTX 4090 | 预计 2.4–6.4M；保守上限约 10.5M | 预留 5–10 GB | 6–12 GPU 服务小时 |

保守上限按 160 tasks × 8 generations × 8192 context tokens 估计，不代表预期实际消耗。
共享 GPU 排队可能使正式采集跨 3–5 个日历日。

## 15. Preflight

每个真实模型 run 前必须检查：

- protocol/config/schema version；
- catalog/split hash；
- Git 状态摘要；
- smolagents/openai 版本；
- Prompt/Tool/Verifier hash；
- Checkpoint v2 测试结果；
- Worker model ID/version；
- TLS/CA/API Key；
- GPU 授权和映射；
- 本地/远程磁盘；
- 输出目录不存在；
- append/resume guard；
- 最大 task/step/token/time；
- 停止和资源释放方案。

## 16. B2/B3/B4 Gate

### B2

- 5/5 完整 Agent Checkpoint；
- messages/tool-call/Observation 可审计；
- Agent/Environment/combined Restore hash 100%；
- first resumed model input exact-match；
- v1 兼容测试通过。

### B3

- 5/5 恢复后真实 Qwen CONTINUE；
- 不增加干预 Prompt；
- outcome/infra/verifier 分类完整；
- Token/Latency 缺失与 0 区分；
- 每个 label 可追溯。

### B4

- 8–12 tasks；
- 四领域、至少四类故障；
- 至少 5 个有效 Checkpoint；
- 无跨分支串扰；
- 资源正确释放；
- `formal_experiment=false`；
- smoke 未进入正式训练集。

任何 Gate 失败即停止，不得降低标准进入正式 Train。

## 17. 当前状态

```text
B0 audit: PASS
B1 protocol: NOT STARTED
B2 checkpoint implementation: NOT STARTED
B3 model-backed Continue: NOT STARTED
B4 smoke: NOT STARTED
B5–B8 formal collection: BLOCKED
```

本计划不授权启动 GPU、修改研究设计或实施代码。下一步是用户确认 B1/B2 方案。
