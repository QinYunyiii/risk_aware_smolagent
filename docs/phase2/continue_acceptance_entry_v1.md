# Frozen CONTINUE Acceptance Entry v1

日期：2026-11-27。研究协议：risk-continue-v2.1.0；实现：readonly-refresh-v1。

## 范围与版本

入口：`scripts/run_frozen_continue_acceptance.py`。
父清单仍为 `protocol_freeze_manifest_v2_1_readonly_refresh.json`，Hash：
`c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7`。
原先冻结的 99 个文件不修改。新增接线代码、离线 Provider、CLI、范围配置、本文及
复用的既有 API/远端证据核验辅助脚本
由 `continue_acceptance_entry_manifest_v1.json` 单独固定，启动时校验父清单及本清单。

这是非正式验收入口。所有产物 formal_experiment=false、research_dataset_eligible=false；
通过不直接授权正式采集，也不将历史 Smoke 转成训练数据。

## 唯一流程

固定 T1–T5 Train 顺序及 Worker 0/1/0/1/0，配置见
`configs/continue_acceptance_v2_1.yaml`。每任务最多一次 Source Attempt，
任一失败立即停，零自动重试、零替换、零 Failover。

1. 加载冻结配置，检查输入 Hash；为唯一 Run 创建独立本地目录。
2. Source 使用实际 Agent stream 和本地工具，每完成一个 ActionStep 都检查捕获条件。
3. 首个合格非终止 Observation/Error 后同步捕获，立即关闭 Source stream/client。
   不发送额外受控恢复请求；无合格点如实停止，不能等第二次 Observation。
4. 使用原 CheckpointManager 独立 Restore；原 ContinueGateRunner 校验六类 Hash、
   首次实际模型输入和 readonly-refresh Guard，真正执行 resume(stream=True) 到终止。
5. 原确定性 Verifier 判定结果；统一入口另外检查 Provider Audit、Source 不变及资源释放。
6. 保存任务结果和 Gate；最终资源审计完成后发布最终 Gate 和完整 Artifact Manifest。

## 预算与可观察性

使用父冻结预算：10 Agent steps / 10 model calls / 12 tool calls / 50,000 total tokens，
单次 Completion 1024、Context 8192、请求 120 秒、任务 wall-clock 900 秒。
Source 和 CONTINUE 使用同一配置；已消耗预算不清零。

新增的 AcceptanceRecordingModel 是现有 RecordingModel 的薄适配，Source/CONTINUE
均使用同一适配类。它按同一任务起点控制 wall-clock，将请求 timeout 进一步限制到
剩余时间，并在返回 Agent 执行工具前检查实际 Provider 用量未超 Context。
不改变推理参数、Prompt 或工具 Schema。Provider 实际 token usage 是权威依据；
没有本地 Qwen tokenizer，不声称发送前已精确计算输入 Token。服务端拒绝请求按服务
失败保留，不能伪造为有效 Agent 标签。

Provider Response、原 SDK 可见响应快照及其 Hash、Usage、Finish Reason、Latency、
Parsing Outcome 和模型请求 ID 均使用原 ProviderResponseAudit 保存。
这不是 HTTP wire capture，不保存鉴权 Header。

如果请求被手动中断，先实际关闭本次客户端，再释放由本入口跟踪的中断请求标记，
使原 ConnectionLifecycle 可以清理本次隧道。不会伪造 Provider 响应或成功结果。

原组件输出保留；对外的 `task_result.json.accepted_y_continue` 只有完整恢复、精确输入、
Verifier、审计链及资源检查成立时才接纳组件的 0/1，否则为 null。
例如基础设施或审计持久化中断不能被入口接纳为 Agent 失败；不会回写修改组件证据。
有效 Verifier=false 可以保留非正式的 0，同时停止本次 Gate。

## 模式

仅本地预检（默认；不读取 Key，不创建连接）：

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode local-preflight
```

完整离线五任务（使用独立 MockTransport SDK 客户端，不访问真实 Key 或 CA）：

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode offline-smoke
```

离线 Provider 是唯一读取 Reference Steps 和 expected 来生成模拟回答的模块；
真实 Source/CONTINUE 不读取它们来选择动作或替代模型。离线模式安装网络/远端工具
拒绝 Hook。Mock 响应、Usage 和标签均明确为 Fixture，不能作为真实效果证据。

## 后续真实验收（本轮不执行）

须用户另行批准，仅使用重新核验的现有 Worker。沿用既有远端身份、权重、版本、
GPU 映射证据格式和 ConnectionLifecycle。证据必须另有 checked_at（带时区，
不超过 300 秒）及 acceptance_run_id（精确绑定本 Run ID），防止误用旧文件。
既有 worker_ownership.observed_at 和两次 GPU 采样时间也必须在同一新鲜度窗口内；
每个 Worker 的容器、用户、存活及 Listener 所有权逐项检查。阈值和目标身份集中在
入口配置的 preflight 节中。鉴权凭据和 CA 路径只在获准真实模式检查，不写出值。
SSH 身份、Container、Worker 所有权和 GPU/权重核验由授权预检产生，不能凭旧 PASS
自动认定当前服务归属。TLS 和 /v1/models 在受控隧道建立后重新预检。

准备好本次私有证据文件和 Connection Spec 后，唯一真实入口形如：

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance.py \
  --mode real-five-task-gate --authorize-remote \
  --run-id <本次唯一ID> \
  --remote-evidence <本次私有远端核验证据.json> \
  --connection-spec <本次私有连接配置.json>
```

没有独立授权标志或缺少新证据，入口在创建实验目录和模型客户端之前拒绝。
不启动或重启 vLLM，不分配 GPU，不修改远端配置；只清理本次拥有的连接资源。
运行产物均为新的目录，不覆盖历史 Run。

## 产物与 Gate

- run_manifest.json：Git HEAD 与工作区是否有未提交修改、父/入口清单 Hash、模型参数、
  Catalog/Split 和执行范围。Source task_run_id 含本次 Run ID，避免跨 Run 同名关联。
- entry_manifest_snapshot.json / entry_inputs/：保存本次入口清单与其固定输入的精确副本。
- tasks/<task_id>/：捕获决策、Source 模型/工具记录、Source 状态、任务结果和资源释放。
- source/checkpoints/：完整 Checkpoint v2。
- continue/：独立恢复分支、完整 Agent/模型/工具轨迹、原 Verifier/结果和累计预算。
- audit/：Source 与 CONTINUE 的原 Provider / Parsing 审计。
- gate_summary.json：组件流程结果。
- **gate_summary_final_audit.json**：连接清理完成后的权威 Gate。
- artifact_manifest.json：所有已发布文件的 SHA-256；不将自身纳入以避免循环。

本地预检为 CONTINUE_ENTRY_PREFLIGHT_PASS；离线完整验收为 CONTINUE_ENTRY_OFFLINE_PASS；
只有获准真实模式成功才能产生 REAL_CONTINUE_GATE_PASS。任何失败为
CONTINUE_ACCEPTANCE_BLOCKED。所有模式正式采集就绪均保持 false，等待单独审核。
