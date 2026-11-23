# B4 Smoke Test — Stage A Report

日期：2026-11-23（Asia/Shanghai）
最终 Stage A Run：`b4_stage_a_20261123_002`
Protocol：`risk-continue-v2.0.0`
阶段范围：仅本地离线工程实现与验收

## Objectives

- 为 B4 组合复用 B2 Primary Checkpoint 和 B3 完整 CONTINUE，不复制核心协议。
- 冻结 8–12 个非正式 Smoke 任务的四领域、多故障编排。
- 离线验证 Verifier、F6 Retry 安全、预算连续性、分支隔离、数据排除和远程授权门。
- 保留 B3 的四条 `Y_continue=0`，不修改 Verifier、标签或历史轨迹。
- Stage A 完成后停止，不连接远程服务，不进入真实 Qwen Stage B。

## Git 状态

- Baseline / Current HEAD：`2d9125687f7647a1c186d1263810e932867aedda`
- Branch：`master`
- Stage A 开始时已存在未提交的 B3 实现和报告；全部保留。
- 预先存在的 `docs/phase2/daily_reports/b2_day5_real_qwen_report.md` 未被修改或视为 B4 产物。
- 未执行 reset、clean、checkout、rebase、Commit 或 Push。
- B4 与尚未提交的 B3 核心代码存在依赖，且 B3 原任务禁止自动提交；因此本阶段不把两阶段修改混合提交。

## 环境状态

- 工作目录：`/home/three/projects/risk-aware-smolagents`
- Python：3.12.3
- smolagents：1.26.0
- OpenAI SDK：3.52.0
- httpx2：2.16.2
- 本地磁盘：约 943 GiB 可用，高于 20 GiB Gate。
- Stage A 网络请求：0。
- SSH / GPU / vLLM / HTTPS API / Tunnel：均未访问、查询、启动或停止。
- 外部 Recovery-Bench：未访问、未修改。

## 修改文件

新增：

- `configs/b4_smoke.yaml`
- `src/risk_agent/collection/b4_smoke_gate.py`
- `scripts/run_b4_smoke_gate.py`
- `tests/agent/test_b4_smoke_gate.py`
- `docs/phase2/daily_reports/b4_stage_a_report.md`

向后兼容扩展：

- `src/risk_agent/faults/injector.py`
  - 保存和恢复 FaultInjector identity、调用计数、最近结果、事件历史及随机状态。
- `src/risk_agent/collection/benchmark_tools.py`
  - 本地工具可选接入 FaultInjector；记录 pending operation、Agent 可见错误和提交状态。
- `src/risk_agent/collection/day5_gate.py`
  - B2 默认行为保持不变；新增可选 B4 experiment flags 与 fault adapter capture/restore。
  - 增加 Source 请求 latency 和 Checkpoint 剩余预算记录。
- `src/risk_agent/collection/continue_gate.py`
  - B3 默认行为保持不变；新增可选 fault adapter restore factory 与 experiment ID。
  - 增加 CONTINUE 后剩余预算记录。
- `src/risk_agent/collection/__init__.py`
  - 导出 B4 配置、preflight 和 Runner 接口。

未修改研究问题、动作语义、故障定义、Verifier 成功标准、预算上限、Prompt、Tool Schema、Split 或 Checkpoint Schema。

## B2 / B3 前置 Gate

- B2 Day 5：PASS，5/5 Primary Checkpoint。
- B3 Stage B：PASS，5/5 完整 CONTINUE 链路。
- B3 历史标签保持：`Y=1` 为 1、`Y=0` 为 4、`null` 为 0。
- 五个 B2 Checkpoint 的文件集合、大小和 SHA-256 与 B3 Stage A 基线逐项一致。
- B4 未重建、覆盖或改写 B2/B3 Run。

## 任务分配计划

所有任务保留原始 `split=train`，同时强制设置：

```json
{
  "formal_experiment": false,
  "execution_mode": "model_backed_smoke",
  "controller_type": "qwen3_model",
  "research_dataset_eligible": false,
  "exclude_from_formal_datasets": true
}
```

| Task ID | Domain | Fault | 目标工具 | Checkpoint 条件 | CONTINUE | Verifier |
|---|---|---|---|---|---|---|
| `data_filter_category_eq_001` | Data Analysis | F0 | `filter_data` | 首个合格非终止 Observation | 是 | DeterministicVerifier |
| `data_aggregate_units_sum_001` | Data Analysis | F3 | `aggregate_data` | 同上，错误对 Agent 可见 | 是 | DeterministicVerifier |
| `file_list_project_tree_001` | File Retrieval | F0 | `list_files` | 首个合格非终止 Observation | 是 | DeterministicVerifier |
| `file_search_project_code_001` | File Retrieval | F1 | `search_files` | 同上，Timeout 对 Agent 可见 | 是 | DeterministicVerifier |
| `api_inventory_primary_001` | API Workflow | F2 | `query_inventory` | 同上，503 对 Agent 可见 | 是 | DeterministicVerifier |
| `api_order_pending_read_001` | API Workflow | F4 | `get_order` | 同上，返回 stale observation | 是 | DeterministicVerifier |
| `api_order_start_processing_001` | API Workflow | F6 committed | `update_order` | 写入已提交但响应丢失后 | 是 | DeterministicVerifier |
| `api_order_start_processing_002` | API Workflow | F6 not_committed | `update_order` | 确认未提交的错误后 | 是 | DeterministicVerifier |
| `logic_calculate_addition_001` | Calculation & Logic | F3 | `calculator` | 同上，错误对 Agent 可见 | 是 | DeterministicVerifier |
| `logic_convert_metres_kilometres_001` | Calculation & Logic | F0 | `unit_converter` | 首个合格非终止 Observation | 是 | DeterministicVerifier |

覆盖结果：10 个任务、4 个领域、6 种故障类型（F0/F1/F2/F3/F4/F6）。F5 需要第二次重复调用，而当前选定 Catalog 的 reference path 是单次工具调用；Stage A 未人为制造与任务契约不一致的重复动作。B4 Gate 只要求至少四种故障，本计划已超过该要求。

## B4 Runner

- `local-preflight` 是默认模式，不加载远程凭据，也不构造远程 Client。
- `remote-preflight` 和 `smoke-gate` 在创建输出目录前要求单独的 Stage B 授权开关。
- 真实执行路径按任务顺序组合：
  1. B2 `Day5GateRunner` 捕获 Primary Checkpoint；
  2. 保存并独立恢复 Fault Adapter；
  3. 完成六类 Hash 与首次输入校验；
  4. B3 `ContinueGateRunner` 调用完整 `resume(stream=True)`；
  5. DeterministicVerifier 生成 `1/0/null`；
  6. 保存 task/checkpoint/model/tool/budget/verifier/lineage 审计。
- 已完成工具签名仍由 B3 Replay Guard 阻止，F6 committed/unknown 不会被自动盲目重放。
- 所有 Run 使用 `exist_ok=False` 的新目录；JSONL 使用 append-only writer。
- 远程 Evidence 必须包含当前授权、HTTPS、模型身份、Worker/GPU 映射及至少两次 GPU 采样。

## Verifier 测试

对 10 个计划任务逐项执行：

- Reference 正向结果：10/10 通过。
- 故意错误结果：10/10 被拒绝。
- 结果缺失 `None`：10/10 被拒绝。
- 正确内容但类型不匹配：10/10 被拒绝。
- 模型服务失败：保持 `Y_continue=null`，不创建 Verifier Outcome，不误记为 Agent 失败。

B3 的四条真实 `Y=0` 未被重新计算、转换或覆盖。

## F6 安全测试

| Commit status | Blind Retry | State check | 结果 |
|---|---|---|---|
| `not_committed` | 允许 | 否 | PASS |
| `committed` | 阻止 | 是 | PASS |
| `unknown` | 阻止 | 是 | PASS |

离线集成测试还验证 FaultInjector 状态可恢复到两个独立分支；推进一个分支的调用计数和随机状态不会改变另一个分支。

## Checkpoint、CONTINUE 与隔离测试

- 通过 MockTransport 在生产代码路径上执行了一条 F3 Agent 可见错误链路。
- Source Qwen Tool Call → 故障 Observation → Checkpoint v2 → B2 controlled request → 独立 Restore → B3 完整 CONTINUE → Verifier 全链路通过。
- 首次恢复输入 exact-match：PASS。
- Fault Adapter Checkpoint 状态：PASS。
- CONTINUE Verifier：PASS，测试标签 `Y_continue=1`。
- 已完成 Source Tool Replay：0。
- Checkpoint Mutation：0。
- Generator close 额外请求：0。
- Workspace、SQLite、Runtime、Memory 和 Adapter 隔离由 B2/B3 回归矩阵继续覆盖。

该 MockTransport 结果只用于 Stage A 工程测试，不冒充真实 Qwen B4 结果。

## Budget 与成本记录

- 冻结上限保持：10 Agent Steps、10 Model Calls、12 Tool Calls、50,000 Tokens、900 秒 wall budget、256 Completion Tokens/Call。
- Source、controlled Restore 和 CONTINUE 请求分别记录 Prompt/Completion Tokens、Model Latency 和可观测状态。
- Source/CONTINUE 记录剩余 Model Calls、Tool Calls、Tokens 和 Latency。
- 工具记录实际 Tool Latency；没有发生工具调用时允许真实聚合成本为 0。
- Provider 不可观测的成本保持 `null`，不转换为 0。
- Stage A 基础设施失败探针验证 `null` 与任务失败标签 0 严格区分。

## Smoke 数据隔离

- 计划使用现有 Train Task 仅作为专用 Development Smoke 输入。
- 所有 B4 记录均为 `formal_experiment=false`、`model_backed_smoke`、`research_dataset_eligible=false`。
- `exclude_from_formal_datasets=true` 是强制配置且通过 preflight。
- B4 产物不得进入正式 Train / Validation / Test、风险模型训练、阈值选择或预算调优。

## Artifacts Generated

- 首轮只追加 preflight：`runs/phase2_b4_smoke/b4_stage_a_20261123_001/`
- 最终 Stage A preflight：`runs/phase2_b4_smoke/b4_stage_a_20261123_002/`
- 最终 Run 包含 run manifest、任务计划、Catalog 审计、B2/B3 前置 Gate 审计、Verifier 探针、F6 安全探针、infra-null 探针、Gate summary 和 artifact manifest。
- 最终 artifact manifest：46/46 文件存在且大小、SHA-256 匹配。
- B4 plan file SHA-256：`91b766f3a498bed386f9d35a9300a1535c0d66801cddfcd268dcfee9f6ef1795`。
- Canonical Catalog SHA-256：`8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775`，匹配冻结值。

首轮 Run 未被覆盖；代码补强后使用新 Run ID 重新生成最终证据。

## Commands and Tests

| 命令 | Exit | 结果 |
|---|---:|---|
| `.venv/bin/python scripts/run_b4_smoke_gate.py --mode local-preflight --run-id b4_stage_a_20261123_002` | 0 | Stage A Gate PASS；网络请求 0 |
| `.venv/bin/python -m pytest -q tests/agent/test_b4_smoke_gate.py --disable-warnings` | 0 | 4 passed |
| B4 + Fault + B2/B3 wiring 专项组合 | 0 | 24 passed |
| Checkpoint v2、隔离、Budget、Retry、B3/B4、Fault、Branch、Catalog 回归 | 0 | 144 passed |
| `.venv/bin/python -m pytest -q --disable-warnings` | 0 | 246 passed in 15.70s |
| `.venv/bin/python -m compileall -q src tests scripts/run_b4_smoke_gate.py` | 0 | PASS |
| `git diff --check` | 0 | PASS |
| B4 文件及最终 Run 敏感信息模式扫描 | 0 | 无私钥、Token、Bearer 或 API Key 值命中 |
| 最终 artifact manifest 独立复算 | 0 | 46/46 PASS |
| B2 Checkpoint inventory 独立复算 | 0 | 5/5 unchanged |

## Acceptance Checklist

- [x] B2/B3 前置 Gate 真实产物复核通过。
- [x] B4 Runner 本地离线执行通过。
- [x] 10 个任务编排完成，覆盖四领域。
- [x] 覆盖 6 种故障，包含 F0、Agent 可见错误和 F6 部分成功。
- [x] Checkpoint v2 与 B3 CONTINUE 离线生产路径集成通过。
- [x] Verifier 正向、负向、类型不匹配、缺失探针通过。
- [x] 基础设施失败未误标为 Agent 失败。
- [x] F6 committed/unknown Blind Retry 被阻止。
- [x] Budget continuity 和 null-vs-zero 测试通过。
- [x] Workspace/SQLite/Runtime/Memory/Fault Adapter 隔离回归通过。
- [x] Smoke 数据排除机制通过。
- [x] 敏感信息扫描通过。
- [x] 全仓测试、compileall 和 diff check 通过。
- [x] Stage A 未进行任何远程操作。

## Gate 结果

```text
B4 Stage A: PASS

Planned Smoke Tasks: 10
Domains: 4/4
Fault Types: 6 (F0/F1/F2/F3/F4/F6)
Planned Primary Checkpoints: 10
Planned CONTINUE Chains: 10
Verifier Probes: PASS
F6 Safety: PASS
Smoke Exclusion: PASS
Network Requests: 0

B4 Stage B: NOT AUTHORIZED / NOT STARTED
B4 Final Gate: NOT YET EVALUATED
Formal Data Collection: NOT AUTHORIZED
```

## 遗留问题

- Stage A 未生成真实 B4 Primary Checkpoint、真实 B4 CONTINUE 标签或真实成本；这些只能在单独授权的 Stage B 中产生。
- 当前远程 Worker、TLS、模型权重和 GPU 状态没有在 Stage A 复用历史证据宣称为当前有效；Stage B 必须重新审计并进行两次 GPU 采样。
- F5 未进入本轮计划，因为选定任务 reference path 不包含第二次重复调用。未伪造 F5；B4 的至少四故障 Gate 仍由六种实际计划覆盖满足。
- 真实 Qwen 可能不按 reference path 调用计划工具，或直接给出 Final Answer；这种情况必须保留为未捕获 Checkpoint 的真实 Attempt，不能改 Prompt 强行通过。
- B3 的格式型 `Y=0` 现象可能在 B4 再次出现，必须按冻结 Verifier 如实记录。

## Stage B 所需资源与授权

- 必须收到精确授权文本：`授权执行 B4 Stage B 真实 Qwen Smoke Test`。
- 重新确认 SSH 主机身份、入口容器、Worker 所有权、PID/cmdline、权重 Hash、TLS、`/v1/models` 和两个时间分离的 GPU 样本。
- 优先复用合格既有 Worker；未经额外资源批准不启动、重启或停止 vLLM。
- 名义请求量约 30 次：每任务 1 次 Source、1 次 B2 controlled Restore、至少 1 次 B3 CONTINUE；实际由模型多轮行为决定。
- 冻结最坏上限为每任务 Source 1 + controlled 1 + CONTINUE 最多 9 次 Model 请求，即 10 任务最多 110 次；工具调用仍受每任务 12 次上限约束。
- Stage B 必须先运行远程 preflight，再执行单任务完整验收，成功后才继续剩余任务。
- 结束后只关闭本次创建的 Tunnel/进程，保留既有 Worker，并记录最终 GPU/端口状态。

## Remaining Work

等待用户审核 Stage A 代码、配置、测试和产物，并决定是否单独授权 B4 Stage B。不得自动开始 B5、正式数据采集、风险/效用训练、RETRY 或 REPLAN 批量实验。

## Status: PASS

此状态仅表示 **B4 Stage A 本地工程 Gate PASS**；不表示 B4 最终 Gate 或任何研究结论通过。

## Git Commit

未创建 Commit，未 Push。原因是 Stage A 强制停止边界，以及当前工作区包含受保护且未提交的 B3 前置实现；等待用户审核后再决定交付方式。
