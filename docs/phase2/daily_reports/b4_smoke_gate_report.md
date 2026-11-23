# B4 Real Qwen Smoke Gate Report

日期：2026-11-23（Asia/Shanghai）
Protocol：`risk-continue-v2.0.0`
最终 Run：`b4_smoke_gate_20261123_003`
最终状态：**PASS**

## Objectives

- 在真实 Qwen3-8B 上组合验证 B2 Primary Checkpoint、独立 Restore、B3
  `resume(stream=True)`、确定性 Verifier 和 Outcome Lineage。
- 使用 10 个非正式 Smoke 任务覆盖四领域、F0 和多种 Agent 可见错误。
- 验证预算连续性、分支隔离、F6 安全规则、零工具重放及 Smoke 数据排除。
- 不修改冻结 Prompt、Verifier、标签、任务 Split、预算或历史 B2/B3 产物。

## Git Status

- HEAD：`2d9125687f7647a1c186d1263810e932867aedda`
- Branch：`master`
- B4 开始前工作区已有受保护的未提交 B3 实现和报告；全部保留。
- 未执行 reset、clean、checkout、rebase、Commit 或 Push。
- B4 修复直接依赖尚未提交的 B3 `continue_gate.py`，无法在不混合 B3 前置工作
  的情况下创建独立 B4 Commit。

## Stage A

- Stage A Run：`b4_stage_a_20261123_002`。
- 10 个计划任务、4 个领域、6 种计划故障（F0/F1/F2/F3/F4/F6）。
- Verifier 正向、负向、类型不匹配、缺失和基础设施 `null` 探针通过。
- F6 `not_committed/committed/unknown` 安全探针通过。
- Stage A 没有 SSH、GPU、vLLM、远程 API 或真实 Qwen 操作。
- 详细结果见 `docs/phase2/daily_reports/b4_stage_a_report.md`。

## Remote Preflight

- 用户明确授权文本：`授权执行 B4 Stage B 真实 Qwen Smoke Test`。
- SSH 目标：`root@121.48.161.243:4035`；严格 `known_hosts` 和专用审计密钥。
- 实际入口：Docker 容器 `36f825bcc5da`，Ubuntu 22.04；宿主 OS 未审计。
- 初始 GPU 采样：2026-11-23 19:45:42 和 19:47:34（Asia/Shanghai）。
- 最终 GPU 采样：2026-11-23 20:07:39（Asia/Shanghai）。
- 复用两个健康且所有权可由项目状态文件、PID、cmdline、端口和
  `CUDA_VISIBLE_DEVICES` 一致证明的既有 Worker；B4 未启动、重启或停止 vLLM。

| Worker | Remote PID | Physical GPU | GPU UUID | Endpoint |
|---|---:|---:|---|---|
| worker-0 | 382420 | 0 | `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160` | `https://localhost:8000/v1` |
| worker-1 | 382422 | 1 | `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d` | `https://localhost:8001/v1` |

- Model ID：`Qwen/Qwen3-8B`。
- Model Version：`Qwen3-8B-modelscope-snapshot`。
- vLLM 0.34.0；Torch 2.14.0+cu132；Transformers 5.22.2。
- `config.json`、index 和五个 safetensors 分片 SHA-256 全部匹配 B2/B3 冻结身份。
- TLS 使用仓库外 CA 严格校验；证书 SAN 包含 `localhost` 和 `127.0.0.1`。
- 两个 Endpoint 未鉴权均返回 401；鉴权 `/v1/models` 均返回目标 Model ID。
- 未使用 `verify=False`、`curl -k`、公网无鉴权端口或备用主机。

## Canary

独立 Run `b4_single_chain_20261123_001` 先完成一条完整链路：

- Primary Checkpoint：1/1。
- Exact First Input：1/1。
- Complete CONTINUE：1/1。
- Tool Replay / Checkpoint Mutation：0 / 0。
- 成本缺失率：0%。
- 标签：`Y_continue=0`；Verifier 失败是模型返回类型/形状结果，不是链路失败。
- Artifact manifest：55/55 文件存在且 SHA-256 匹配。

Canary 通过后才开始 10 任务批量 Gate。

## Preserved Failed Runs

所有失败 Attempt 和 Run 均只追加保留，没有删除、改写或从统计中隐藏。

### `b4_smoke_gate_20261123_001`

- 状态：BLOCKED。
- 10 Attempts；8 个有效 Checkpoint；3 条完整 CONTINUE。
- worker-1 本地 SSH 隧道中断，最后一条分支两次安全 pre-tool 重试均失败。
- Prompt/Completion Token 缺失率 6.67%，超过 5% Gate。
- 同时暴露 Replay Guard 分类缺陷：有错误、未完成的工具尝试被误当作已完成调用。
- Artifact manifest：401/401 文件存在且 SHA-256 匹配。

### Engineering Fix

修复只纠正“已完成调用”的分类，不改变动作、Prompt、Verifier 或标签：

- 成功完成的 Source 调用继续禁止重放。
- 写操作 `committed/unknown` 或缺少提交证据继续禁止盲目重放。
- 只读失败，以及写操作 `not_started/not_committed`，不再被误标为已完成调用；
  Agent 可按原始 Prompt 自然继续。
- 新增 committed、unknown、not_committed 和只读失败回归测试。
- 修复后 B3/B4 专项：19 passed。

### `b4_smoke_gate_20261123_002`

- 状态：BLOCKED。
- 修复后的前三条 CONTINUE 已完整通过。
- 复用的旧单一 ControlMaster 随后整体断开，剩余任务成为基础设施失败。
- 4 个有效 Checkpoint、3 条完整 CONTINUE；Artifact manifest 206/206 匹配。
- 该 Run 证明 Replay Guard 修复有效，但不满足最终 Gate。

在第三个 Run 前，关闭失效的本地控制状态并创建两个全新的独立 SSH
ControlMaster；两个 Endpoint 再次通过 TLS 和模型身份检查。远程 Worker 未改变。

## Final Real Qwen Run

最终 Run：`b4_smoke_gate_20261123_003`。

| Metric | Result |
|---|---:|
| Task Attempts | 10 |
| Domains | 4/4 |
| Actual observed fault types | 5: F0/F1/F2/F3/F4 |
| Agent-visible error Checkpoints | 4 |
| Valid Primary Checkpoints | 8 |
| Complete real Qwen CONTINUE chains | 8 |
| Six-hash Restore matches | 8/8 |
| Exact first model input | 8/8 |
| Outcome lineage | 8/8 |
| `Y_continue=1` | 1 |
| `Y_continue=0` | 7 |
| `Y_continue=null` | 0 |
| Tool Replay | 0 |
| Checkpoint Mutation | 0 |
| Branch Cross-talk | 0 |
| Harness Error | 0 |
| Prompt/Completion/Model/Tool/Wall missing rate | 0% / 0% / 0% / 0% / 0% |

### Per-task CONTINUE Results

| Task | Worker | CONTINUE model calls | New tools | Verifier | Y |
|---|---|---:|---:|---|---:|
| `data_filter_category_eq_001` | worker-0 | 1 | 0 | fail | 0 |
| `data_aggregate_units_sum_001` | worker-1 | 3 | 2 | fail | 0 |
| `file_list_project_tree_001` | worker-0 | 1 | 0 | fail | 0 |
| `file_search_project_code_001` | worker-1 | 2 | 1 | fail | 0 |
| `api_inventory_primary_001` | worker-0 | 2 | 1 | fail | 0 |
| `api_order_pending_read_001` | worker-1 | 1 | 0 | fail | 0 |
| `logic_calculate_addition_001` | worker-0 | 2 | 1 | pass | 1 |
| `logic_convert_metres_kilometres_001` | worker-1 | 1 | 0 | fail | 0 |

所有八条链路都以真实 Qwen `final_answer` 终止，然后执行冻结的
DeterministicVerifier。七条 `Y=0` 原样保留，没有类型强制转换、标签重写或成功标准修改。

## F6 Result

- 两个 F6 Source Attempt 均由 Qwen 首先调用只读 `get_order`。
- Protocol v2 在首次合格非终止 Observation 后捕获 Checkpoint，因此模型尚未到达
  计划的 `update_order`，F6 fault schedule 未触发。
- 两条 Attempt 被明确记录为 `fault_schedule_satisfied=false`，没有冒充有效 Checkpoint，
  也没有进入 CONTINUE 分母。
- 未更改 Checkpoint 触发条件或 Prompt 来强行制造 F6。
- Stage A 的 committed/unknown Blind Retry 禁止规则仍通过；真实 Run 没有发生不安全写入。
- 最终 Gate 要求至少四种实际故障；本轮实际覆盖 F0/F1/F2/F3/F4 共五种，因此核心标准满足。

## Requests and Costs

最终 PASS Run：

| Phase | Completion attempts | Prompt tokens | Completion tokens | Model latency |
|---|---:|---:|---:|---:|
| Source + controlled Restore | 20 | 31,770 | 778 | 58.535 s |
| Full CONTINUE | 13 | 22,708 | 555 | 24.134 s |
| Total | 33 | 54,478 | 1,333 | 82.670 s |

- Source/controlled 请求在两个 Worker 上各 10 次。
- CONTINUE 请求：worker-0 为 6 次，worker-1 为 7 次。
- Source/controlled task wall 合计 59.345 秒；CONTINUE wall 合计 24.370 秒。
- Source 工具调用 10 次；CONTINUE 新工具调用 5 次；全部工具 latency 可观测。
- 每任务继续使用冻结上限：10 Agent Steps、10 Model Calls、12 Tool Calls、
  50,000 Tokens、900 秒 wall、256 Completion Tokens/Call。

整个 Stage B（Canary、两个保留失败 Run、最终 PASS Run）：

- Completion attempts：81。
- 有 Usage 的请求：77；四个连接失败请求保持 Token 为 `null`。
- 可观测 Prompt Tokens：127,689。
- 可观测 Completion Tokens：3,343。
- 可观测 Model Latency：403.250 秒。
- TLS 和 `/v1/models` 身份请求不计入 Completion attempt。

## Isolation and Integrity

- 八个有效 Checkpoint 均为 schema v2.0.0，六类 Hash 全匹配。
- 八个恢复分支首次输入均与 Checkpoint canonical bytes/hash 精确匹配。
- Source、Restore Workspace、SQLite、Runtime、Agent Memory 和 Fault Adapter 独立。
- Tool Replay 0；Checkpoint Mutation 0；Branch Cross-talk 0。
- 最终 Run artifact manifest：403/403 文件存在且大小、SHA-256 全匹配。
- 最终 Run manifest 记录 plan、task catalog 和修复后实现文件 SHA-256。
- 所有 B4 记录为 `formal_experiment=false`、`execution_mode=model_backed_smoke`、
  `controller_type=qwen3_model`、`research_dataset_eligible=false`、
  `exclude_from_formal_datasets=true`。

## Resource Release

- B4 启动的远程 vLLM：0。
- B4 停止或重启的远程 vLLM：0。
- 最终检查时两个既有 Worker PID 382420/382422 均存活。
- 最终 GPU 0/1 分别约使用 21,781/21,779 MiB，采样利用率均为 0%。
- 最终两个本次创建的独立 SSH ControlMaster 已关闭。
- 清理后本地端口 8000/8001 无监听。
- GPU 2–7 和其他用户进程未修改。
- Checkpoint、失败 Attempt、轨迹和审计证据全部保留。

## Commands and Tests

```text
.venv/bin/python scripts/run_b4_smoke_gate.py --mode remote-preflight --authorize-remote ...
.venv/bin/python scripts/run_b4_smoke_gate.py --mode smoke-gate --single-chain --authorize-remote ...
.venv/bin/python scripts/run_b4_smoke_gate.py --mode smoke-gate --authorize-remote --run-id b4_smoke_gate_20261123_003 ...
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts/run_b4_smoke_gate.py
git diff --check
```

- 专项回归：19 passed。
- 全仓回归：247 passed in 16.43s。
- compileall：PASS。
- `git diff --check`：PASS。
- 敏感信息扫描：PASS；无私钥、Bearer 值或 API Key 值命中。

## Acceptance Checklist

- [x] 10 个任务，无未处理 Harness Error。
- [x] 四领域全部覆盖。
- [x] 五种实际故障，包含 F0 和 Agent 可见错误。
- [x] 8 个完整 Primary Checkpoint，超过最低 5 个。
- [x] 8 条完整真实 Qwen CONTINUE，超过最低 5 条。
- [x] 六类 Hash 8/8 匹配。
- [x] 首次输入 8/8 精确匹配。
- [x] Verifier 探针与真实 Outcome Lineage 完整。
- [x] Token、Latency、Remaining Budget 有效；缺失率 0%。
- [x] 无分支状态串扰。
- [x] F6 committed/unknown 不盲目 Retry。
- [x] Tool Replay 0。
- [x] Smoke 永久排除正式数据集。
- [x] 只操作本次拥有的隧道；既有 Worker 保留。
- [x] 全仓测试、compileall 和 diff check 通过。

## Known Issues

- 共享服务器 SSH 入口存在间歇性握手和隧道中断；两个失败 Run 已完整保留并按
  infrastructure failure 解释，未计为 Agent 标签。
- 当前 Source runner 在隧道中断时可能由 smolagents 表现为
  `assistant_tool_call_missing`；失败 Run 的控制台和请求审计保留了底层 Connection Error。
- 真实 Qwen 未在两个 F6 任务的首个 Checkpoint 前到达 `update_order`，因此本轮没有真实
  F6 标签。没有为通过 Gate 修改协议。
- `Y=0` 占 7/8，主要反映模型最终答案类型/形状未满足冻结 Verifier；这些 Smoke 标签不能
  用于正式风险模型训练或结论推断。

## Final Gate

```text
B4: PASS

Tasks: 10
Domains: 4/4
Observed Fault Types: 5
Valid Primary Checkpoints: 8
Complete Real Qwen CONTINUE Chains: 8
Exact Input / Six Hashes / Lineage: 8/8
Y_continue: 1=1, 0=7, null=0
Tool Replay / Mutation / Cross-talk / Harness Error: 0 / 0 / 0 / 0
Formal Data Collection: NOT AUTHORIZED
B5: NOT STARTED
```

## Remaining Work

- 用户审核 B4 代码、失败 Run、最终产物和报告。
- 进入正式数据采集前，仍需单独冻结 v2 多步 Catalog、Split 和 Fault Schedule。
- 不自动开始 B5、正式采集、风险/效用训练、RETRY 或 REPLAN 扩展。

## Status: PASS

该 PASS 仅表示 B4 工程 Smoke Gate 通过，不表示正式训练数据有效或研究结论成立。

## Git Commit

未创建 Commit，未 Push。当前工作区包含 B3 前置实现及 B4 对其的修复，无法安全拆分为
仅 B4 的独立提交；等待用户决定是否将 B3+B4 作为一个整体验收提交。
