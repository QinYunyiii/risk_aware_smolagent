# B2 Day 5 Real Qwen Report

## A. Git 状态

- 执行日期：2026-11-23（Asia/Shanghai）。
- Baseline Commit：`467548ee5b997fa57a9ddd9ae5d2b69c99008da9`。
- Stage A Commit / Current HEAD / `origin/main`：
  `da4bd76bd137b96f7c55ca262d18c085d57f6aa1`。
- 分支：`master`。
- Stage B 开始时工作区干净，`git diff --check` 通过。
- 本次仅新增本报告；未自动 Commit 或 Push。

## B. 环境与权限

- 用户明确授权只读 SSH、GPU/模型/TLS 预检、复用合格服务、必要时在获批空闲
  GPU 上启动最多两个本次拥有的 Worker，以及执行五任务真实 Qwen Gate。
- SSH 入口实际进入容器 `36f825bcc5da`，用户 `root`，Ubuntu 22.04.3 LTS；
  宿主机操作系统未审计。
- 初始 SSH Banner 间歇性超时。经用户明确授权，安装现有审计公钥；公钥指纹为
  `SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A`。未请求、读取或记录密码。
- 所有审计连接显式禁用 SSH 配置中的 `RemoteForward`。
- 两次独立 GPU 采样时间为 07:10:05 和 07:11:11 UTC；实验结束采样时间为
  07:21:21 UTC。

Worker 与物理 GPU 映射：

| Worker | Physical GPU | GPU UUID | Remote PID | Port | 所有权证据 |
|---|---:|---|---:|---:|---|
| worker-0 | 0 | `GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160` | 382420 | 8000 | 状态文件、cmdline、CUDA_VISIBLE_DEVICES=0 一致 |
| worker-1 | 1 | `GPU-aec607a6-84e2-0b80-eddd-996bf48c344d` | 382422 | 8001 | 状态文件、cmdline、CUDA_VISIBLE_DEVICES=1 一致 |

- 两次采样中 GPU 0/1 均约占 21,567 MiB，利用率 0%，对应本项目既有 Worker。
- GPU 2–7 均存在可见计算进程或显存占用，未选择、启动、停止或影响这些 GPU。
- 现有两个 Worker 健康且所有权可验证，因此未启动新 vLLM，也未创建远程目录。
- 本地磁盘可用空间约 943 GiB，超过 20 GiB Gate。

## C. 模型身份

- Served Model ID：`Qwen/Qwen3-8B`。
- 配置 Model Version：`Qwen3-8B-modelscope-snapshot`。
- 实际架构：`Qwen3ForCausalLM`；model type：`qwen3`；dtype：bfloat16。
- 实际权重根目录：
  `/mnt/risk-aware-smolagents/remote_models/Qwen3-8B`。
- vLLM：0.34.0；远程 Torch：2.14.0+cu132；远程 Transformers：5.22.2。
- 本地 smolagents：1.26.0；OpenAI SDK：3.52.0；httpx2：2.16.2。
- Worker 启动参数确认 `--max-model-len 8192`、`--max-num-seqs 1`、
  Hermes Tool Call parser、TLS key/cert，并仅监听远端 localhost。
- 两个远端本机 Endpoint：未鉴权请求 HTTP 401，鉴权请求 HTTP 200。
- 两个本地隧道 Endpoint 均通过 CA、证书链和 localhost 主机名校验；未使用
  `verify=False` 或 `curl -k`。
- 两个 `/v1/models` 均返回目标 Model ID、实际权重根目录和 8192 context。
- OpenAI Client `max_retries=0`；smolagents Retryer `max_attempts=1`。

实际权重身份由 config、index 及五个 safetensors 分片 SHA-256 固定：

| Artifact | SHA-256 |
|---|---|
| `config.json` | `f7c4eadfbbf522470667b797a3c89be2524832d2d599797248dc304fff447c30` |
| `model.safetensors.index.json` | `f9fdbcb91c23971c13ec5d5f2573d2349e8f61f2f049371ec699281748fdb1bc` |
| `model-00001-of-00005.safetensors` | `31d6a825ae35f11fb85b195b4c42c146c051e446433125a215336abdf95cbf5f` |
| `model-00002-of-00005.safetensors` | `5991236cea6fe21f3d43cab0f0e84448734fbbe0789816202989f2ddc9d18282` |
| `model-00003-of-00005.safetensors` | `c5185c4794be2d8a9784d5753c9922db38df478ce11f9ed0b415b7304d896836` |
| `model-00004-of-00005.safetensors` | `b5ee7de71fbf17db3d5704e0c8f2bc7d005ca9e1d7ca2aeb19827b0cfcaa917a` |
| `model-00005-of-00005.safetensors` | `20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff` |

## D. 五任务结果

最终 Run：`b2_day5_five_gate_20261123_001`。

| Task ID | Domain | Worker | Source Requests | Tool Calls | Capture Step | Restore | First Input | Controlled Qwen |
|---|---|---|---:|---:|---:|---|---|---|
| `data_filter_category_eq_001` | Data Analysis | worker-0 | 1 | 1 | 1 | PASS | exact | PASS |
| `file_list_project_tree_001` | File Retrieval | worker-1 | 1 | 1 | 1 | PASS | exact | PASS |
| `api_inventory_primary_001` | API Workflow | worker-0 | 1 | 1 | 1 | PASS | exact | PASS |
| `logic_calculate_addition_001` | Calculation & Logic | worker-1 | 1 | 1 | 1 | PASS | exact | PASS |
| `data_filter_amount_gt_001` | Data Analysis | worker-0 | 1 | 1 | 1 | PASS | exact | PASS |

- 五个 Task ID、Task Run ID 和 Checkpoint ID 均唯一。
- 五个任务均在首次符合条件的非终止 Tool Observation 后同步捕获。
- 五次恢复均只执行一次受控真实 Qwen Generate，响应保存后在新 Tool Call 执行前停止。
- 前置单任务 Run `b2_day5_single_20261123_001` 也为 PASS 并完整保留。
- Stage B 实际 Completion 请求共 12 次：前置单任务 2 次，最终五任务 10 次。

## E. Hash 验证

五个 Checkpoint 均为 schema `2.0.0`，每个包含 27–30 个校验 Artifact。

以下六项在五个任务中全部匹配：

- Agent State Hash：5/5。
- Next Model Input Hash：5/5。
- Environment State Hash：5/5。
- Runtime State Hash：5/5。
- Identity Config Hash：5/5。
- Combined State Hash：5/5。

对每个任务均独立比较了：

1. Source 下一请求 canonical bytes/hash；
2. Checkpoint 权威 canonical bytes/hash；
3. Restore 后实际 `RecordingModel.generate()` 边界 canonical bytes/hash。

三者逐字节和 Hash 均为 5/5 exact。完成后再次调用
`CheckpointManager.verify_v2()`，五个 Checkpoint 全部通过。

## F. Budget 验证

- 最终五任务真实 Provider 请求：10（Source 5 + Restore Controlled 5）。
- 每个 Source 捕获时 Model Calls = 1；每个 Restore 从 1 恢复并在请求后连续到 2。
- Source Prompt Tokens：7,752；Source Completion Tokens：173。
- Restore Prompt Tokens：8,334；Restore Completion Tokens：285。
- 最终五任务合计真实 Tokens：16,544。
- Source Runtime latency budget usage 合计：82.574 秒。
- Restore 受控请求 latency 合计：72.838 秒。
- 五任务 wall-clock 合计：155.780 秒。
- Source Task Tool Calls：5；Restore 新增 Task Tool Calls：0。
- 所有 Provider Usage 均真实可观测，无缺失值被写成零。
- 每任务均远低于 Model Calls 10、Tool Calls 12、Total Tokens 50,000 和
  Wall Clock 900 秒；Completion Tokens/Call 保持 256。
- 精确预请求 Context Token 数仍未由当前客户端提供；未启用静默截断、压缩或总结，
  Provider Usage 为请求后权威数据。

## G. 副作用审计

Source 实际工具：

| Task | Tool | Error |
|---|---|---|
| `data_filter_category_eq_001` | `filter_data` | none |
| `file_list_project_tree_001` | `list_files` | none |
| `api_inventory_primary_001` | `query_inventory` | none |
| `logic_calculate_addition_001` | `calculator` | none |
| `data_filter_amount_gt_001` | `filter_data` | none |

- Source Tool History：5 条。
- Restore Tool History 新增：0 条。
- 已完成 Tool Call 重放：0。
- Restore Workspace 与 Source Workspace 独立。
- SQLite 恢复使用独立物理文件；本轮 API 任务为只读 `query_inventory`。
- Controlled Response 即使包含新 Tool Call，也未进入 `process_tool_calls`。
- 每次 Restore 前后原始 Checkpoint 文件 inventory 保持不变。
- 不同任务、Worker、RuntimeState、Agent Memory 和本地工具游标未出现串扰。

## H. 失败与异常

- 最终五任务失败 Attempt：0。
- Checkpoint rejected：0。
- Hash/Input mismatch：0。
- Budget rejected：0。
- Provider 自动重试：0。
- Tool Error：0。
- Worker 切换：0。
- 初始 SSH 入口发生间歇性 Banner timeout；在任何模型请求前恢复，不计为 Agent
  任务失败。后续结束采样复用了已验证 ControlMaster，避免新握手不稳定性。
- 日志中的 `Valid model request intercepted before execution` 是源路径冻结输入的预期
  停止信号，不是 Provider 或 Agent 失败。

## I. 资源释放

- 新 vLLM 进程：0；复用两个既有、本项目拥有且健康的 Worker。
- 本次创建本地 SSH ControlMaster：2。
- 本次结束时两个 ControlMaster 均已按所有权记录关闭。
- 清理后本地 8000/8001 无监听。
- 远端 Worker 未由本次创建，因此按授权边界保留，未发送 TERM、未重启。
- 实验结束采样确认原 Worker PID 仍存活；GPU 0/1 约占 21.8 GiB、利用率 0%。
- GPU 2–7 和其他用户进程未修改。
- Checkpoint、轨迹、失败/审计证据均保留。

## J. Final Gate

- B2 Day 5：**PASS**。
- B2 Complete Decision Checkpoint Gate：**满足**。
- 五个真实 Qwen Task Attempts、五个完整 Checkpoint v2、五次独立 Restore、六类
  Hash、五次首次输入精确匹配和五次受控真实恢复请求全部通过。
- 工具重放：0；不安全副作用：0。
- B3 前置工程条件：满足。
- B3：未开始。
- 正式采集：本报告不授权开始；仍需用户按协议单独批准下一阶段。
- `formal_experiment=false`、`execution_mode=model_backed_smoke`、
  `research_dataset_eligible=false`、`y_continue=null`。

## Artifacts

- Remote preflight：
  `runs/phase2_b2_day5/b2_day5_remote_preflight_20261123_001/`
- Single checkpoint：
  `runs/phase2_b2_day5/b2_day5_single_20261123_001/`
- Five-checkpoint Gate：
  `runs/phase2_b2_day5/b2_day5_five_gate_20261123_001/`
- 最终 Run 包含 `run_manifest.json`、`remote_preflight.json`、
  `source_attempts.jsonl`、`checkpoint_index.jsonl`、`restore_audit.jsonl`、
  `model_request_audit.jsonl`、`budget_audit.jsonl`、`tool_trajectories.jsonl`、
  `gate_summary.json`、五个 Checkpoint 和十个 Source/Restore Workspace。

## Commands and Tests

- Stage A 专项测试：6 passed。
- 最终全仓：`228 passed in 14.72s`。
- `.venv/bin/python -m compileall -q src tests`：exit 0。
- `git diff --check`：exit 0。

## Git Commit

- 本报告未提交。
- 本报告未推送。
