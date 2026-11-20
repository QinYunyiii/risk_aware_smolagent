# Workstream B - B0 Audit Report

## Objectives

只读审计现有实现是否能够生成真实 Qwen Agent 风险预测数据，重点检查：

- smolagents 版本和 ToolCallingAgent 循环；
- Agent Memory、messages、tool-call、Observation 生命周期；
- ExecutionMonitor 的 Observation 准确性；
- CheckpointManager 的保存/恢复边界；
- Reference Runner 与模型驱动分支差距；
- Fault Injector 和 Deterministic Verifier；
- vLLM Worker、TLS、SSH tunnel 和资源脚本复用；
- 完整 Agent Checkpoint、CONTINUE 和正式采集的最小方案。

本阶段不修改代码、不运行批量任务、不启动 GPU，不执行 P1-D3。

## Current Baseline

- Git HEAD：`a63d5e0 audit: complete P1-D2 risk feature shortcut analysis`。
- Python：3.12.3。
- smolagents：1.26.0。
- openai：3.52.0。
- Day 8：24 真实 Qwen logical tasks，43 attempts，26 local tool calls。
- Day 9：20 synthetic/reference Checkpoint，60 candidate actions。
- Day 10：20 Train 风险样本，不能作为真实 Qwen 风险证据。
- P1-D2：已确认 `last_failure` 注入 metadata 捷径；严格 Agent-visible 特征仍来自 synthetic history。

审计开始时存在 5 个用户未跟踪文件，本阶段均保持不动。

## Work Completed

1. 完整阅读项目规范、下一阶段指南、工作线 B 指南、P1-D1/P1-D2 审计及 Day 1–14 报告。
2. 核验实际安装的 smolagents/openai 版本与 lock 文件一致。
3. 阅读当前安装包中 `MultiStepAgent.run()`、`_run_stream()`、
   `ToolCallingAgent._step_stream()`、`process_tool_calls()`、`AgentMemory` 和
   `ActionStep.to_messages()` 实现。
4. 阅读 Agent Factory、Day 8 Runner、ExecutionMonitor、RuntimeState、
   CheckpointManager、Reference Branch Runner、Fault Injector、Verifier 和远程管理脚本。
5. 只读抽样 Day 8 真实 Qwen agent steps 与 Day 9 runtime/checkpoint schema。
6. 统计 Day 7 正式 catalog，确认 200/200 当前实例均只有一个 reference step。
7. 统计 Day 8 Token、延迟和正式 run 体积，用作后续资源预算基线。
8. 运行一个无网络、无项目文件写入的 smolagents 生命周期探针，验证 ActionStep stream
   yield 的时序。
9. 形成能力缺口、完整 Agent Checkpoint 设计和真实数据采集计划。

## Files Changed

本阶段仅新增文档：

- `docs/phase2/real_agent_gap_audit.md`
- `docs/phase2/real_agent_checkpoint_design.md`
- `docs/phase2/real_agent_collection_plan.md`
- `docs/phase2/daily_reports/b0_audit_report.md`

未修改：

- `src/`、`tests/`、`configs/`；
- Day 1–14 历史 run、模型或日报；
- 外部 Recovery-Bench；
- P1-D3 文件。

## Artifacts Generated

### `real_agent_gap_audit.md`

保存：

- 当前真实数据链路图；
- smolagents 源码生命周期证据；
- 实际探针命令与原始输出；
- Day 8/9 产物结构与计数；
- 能力与缺口表；
- B0 Gate。

### `real_agent_checkpoint_design.md`

保存：

- Checkpoint schema v2 草案；
- capture/restore/Continue 算法；
- Observation 与 fault metadata 分离；
- 向后兼容和测试 Gate；
- 预计代码范围。

### `real_agent_collection_plan.md`

保存：

- B1–B8 阶段计划；
- execution mode 与 lineage；
- tri-state 标签和无泄漏特征；
- preflight、续跑和资源预算；
- B2/B3/B4 Gate。

## Commands and Tests

### 主要只读命令

```bash
pwd
git status --short
git log -5 --oneline --decorate
wc -l <required documents>
cat/sed <required documents and source files>
rg --files src/risk_agent configs scripts tests
rg -n <call sites and lifecycle symbols>
.venv/bin/python --version
.venv/bin/python -m pip show smolagents openai
```

### 历史产物只读统计

```bash
.venv/bin/python - <<'PY'
# Parse Day 7 catalog, Day 8 JSONL and Day 9 runtime_state.json.
# Calculate counts, token/latency aggregates and directory byte sizes.
PY

df -h .
```

### smolagents 生命周期探针

使用本地 Probe Model + `ToolCallingAgent` + CalculatorTool；不访问网络、不产生正式数据。

原始输出：

```text
{'model_calls_at_capture': 1,
 'memory_step_types': ['TaskStep', 'ActionStep'],
 'action_in_memory': True,
 'observation': '4',
 'message_roles': ['system', 'user', 'assistant', 'tool-call', 'tool-response']}
```

### 未运行

- 未运行 pytest：B0 没有代码修改；历史测试结果仅作为既有证据，不重新声明为本轮通过。
- 未运行远程 API smoke。
- 未执行 SSH。
- 未运行 Docker/Recovery-Bench。
- 未启动、停止或检查远程 vLLM/GPU。
- 未运行正式或批量数据采集。

## Evidence Summary

### smolagents lifecycle

- `run(stream=True)` 返回 `_run_stream()` generator；
- `ActionStep` 完成后先加入 `agent.memory.steps`，再 yield；
- generator 未继续前，下一轮 model call 不发生；
- `write_memory_to_messages()` 在该点包含 Tool Response；
- 该点适合作为 schema v2 捕获点。

### Current monitor/checkpoint

- ExecutionMonitor 保存工具 Python result，不保存下一轮模型实际 message list；
- Day 8 `RuntimeState.agent_memory` 只初始化用户 prompt；
- Day 8 在任务完成后才调用 `get_succinct_steps()`；
- Checkpoint schema v1 没有 Agent payload；
- restore 只返回 `RuntimeState`，不重建 ToolCallingAgent。

### Current branches

- Day 9 controller 是 `deterministic_local_reference`；
- CONTINUE 直接执行 pending tool；
- 没有模型请求；
- Token 为 0；
- 不能作为 Qwen Continue 标签。

### Fault and verifier

- FaultInjector 尚未接入真实 Qwen Tool Adapter；
- F6 当前异常文本存在泄漏 commit truth 的风险；
- Verifier 确定性可复用，但需要 outcome/infra 外层分类。

## Real Qwen Evidence

本轮未生成新真实 Qwen 证据。

继承并只读核验的历史证据：

- Day 2/8 确实通过远程 Qwen3-8B 产生 Tool Call；
- Day 8 正式 run 有 24 logical tasks、43 attempts、26 local tool calls；
- Day 8 没有运行中决策 Checkpoint；
- Day 9–13 没有远程 Qwen 分支调用。

因此当前 formal model-backed risk samples = 0。

## Checkpoint / Restore Audit

现有 CheckpointManager：

- 环境/SQLite/RuntimeState Restore：已有历史工程证据；
- 完整 smolagents Agent context Restore：不支持；
- 恢复后下一轮 Qwen input exact-match：不支持；
- 恢复后 Qwen CONTINUE：不支持。

最小改动是 schema v2 + AgentStateAdapter + checkpointable/resumable ToolCallingAgent，而非
推翻现有环境 CheckpointManager。

## Data Quality

- Day 10 的 20 条样本全部排除为正式真实风险证据；
- 当前 strict Agent-visible proxy 仍来自 synthetic history；
- 当前 Day 7 catalog 全部为单 reference-step；
- 当前不存在独立 Validation/Test 风险 Checkpoint；
- 新数据必须使用独立 run/schema/model namespace。

## Resource Usage

本轮资源使用：

- GPU：0；
- Qwen API：0 calls；
- Docker：0；
- 新实验 Token：0；
- 远程服务变更：0；
- 历史产物修改：0。

历史 Day 8 只读基线：

- 70,333 total observed tokens across 20 token-complete attempts；
- 平均 3,516.7 tokens；
- 平均 latency 10.86 秒；
- 正式 run 约 1.1 MB；
- 本地磁盘约 945 GiB 可用。

## Acceptance Checklist

- [x] 完整阅读指定文档和 Day 1–14 报告。
- [x] 检查 Git 状态并保护用户未提交修改。
- [x] 核验 smolagents/openai 实际版本。
- [x] 阅读 ToolCallingAgent 实际执行循环。
- [x] 确认 Observation 写入 memory 的生命周期节点。
- [x] 用实际探针验证下一模型调用尚未发生。
- [x] 审计 ExecutionMonitor 的准确性边界。
- [x] 审计 CheckpointManager 保存和缺失状态。
- [x] 回答完整 Agent context 能否恢复。
- [x] 审计 Reference Runner 与模型驱动分支差距。
- [x] 审计 Fault Injector 是否接入真实 Agent。
- [x] 审计 Verifier 的标签适用边界。
- [x] 审计 Worker/TLS/tunnel/资源脚本复用条件。
- [x] 区分四种 execution mode。
- [x] 给出最小 Checkpoint 设计、采集方案、测试 Gate 和资源预算。
- [x] 未修改代码、配置、外部仓库或历史产物。
- [x] 未启动远程资源或正式采集。

## Known Issues

1. 完整 Agent Checkpoint 仍未实现。
2. smolagents 1.26.0 没有公开 `AgentMemory.from_dict()`；恢复适配需要受控 subclass/wrapper。
3. 当前 Monitor 不记录 exact Agent-visible Observation message。
4. 当前 RuntimeState budget 不含决策时刻真实模型 Token。
5. FaultInjector 未接入 Day 8 Agent 路径。
6. 当前 200 个正式 catalog 实例均为单步 reference task。
7. 远端 vLLM 固定 8K context，多步骤任务可能达到上限。
8. GPU 自动空闲检查不足以覆盖其他容器 PID namespace，仍需实验室级确认。
9. B1 的标签、fault response、max-step 和模板规模尚未冻结。

## Remaining Work

等待用户确认后，下一步应只进入 B1：

1. 冻结决策时刻；
2. 冻结 tri-state 标签和停止规则；
3. 冻结 fault Agent-visible response；
4. 冻结 Checkpoint schema v2；
5. 冻结模板/split/规模和 8K context 协议；
6. 然后再授权 B2 实施。

不得从本报告自动开始代码修改、GPU smoke 或正式采集。

## Status: PASS

B0 审计 Gate **PASS**：现有能力、缺口、源码证据、最小设计、测试和资源预算已明确。

正式真实风险数据链路状态仍为 **BLOCKED**：完整 Agent Context Restore 与 Qwen
CONTINUE 尚未实现，当前正式合格风险样本为 0。

## Git Commit

未创建 commit。新增文档等待用户检查；未擅自提交。
