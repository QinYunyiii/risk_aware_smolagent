# Workstream B B0：真实 Agent 风险数据链路能力与缺口审计

> 审计日期：2026-11-19（Asia/Shanghai）\
> 项目根目录：`/home/three/projects/risk-aware-smolagents`\
> 审计性质：只读源码、依赖与历史产物审计\
> 代码修改：无\
> 远程操作：无；未访问 SSH、未启动或停止 vLLM/GPU\
> 研究边界：只审计工作线 B，不执行 P1-D3 历史效用审计

## 1. 结论

现有 `CheckpointManager` **不能**恢复 smolagents 的完整 Agent 推理上下文，也不能从
Checkpoint 无损继续 Qwen 的下一轮推理。

当前实现能够保存和恢复：

- 任务 workspace 文件；
- SQLite Backup API 快照；
- Mock API 导出状态；
- 项目自定义 `RuntimeState`；
- 项目自定义 `agent_memory` JSON 列表；
- 工具历史、工具状态、预算、TaskSpec 和 seed；
- artifact checksum 和组合状态 hash。

但当前实现没有保存或恢复：

- 运行中 `smolagents.AgentMemory` 对象的真实 step 序列；
- 下一次模型调用实际使用的完整 `ChatMessage` 列表；
- Assistant tool-call、call ID 与 Tool Observation 的准确消息配对；
- System Prompt、Prompt Template 和工具 schema 的版本化内容；
- Agent 的 `task`、`state`、当前/下一 step、剩余 step budget；
- 模型请求序列、当前 Worker、generation config 的 Checkpoint 级快照；
- FaultInjector 的 call counter、RNG 状态和最近返回值；
- 从上述状态重建 Agent 并继续下一轮 Qwen 调用的入口。

因此，Day 5/6/9 的 hash 一致只能证明“项目自定义任务环境与 RuntimeState”一致，不能证明
“Qwen 下一轮看到的 Agent 上下文”一致。

## 2. 审计输入与保护边界

### 2.1 实际读取的主要文档

- `AGENTS.md`
- `PROJECT_GUIDE.md`
- `README.md`
- `NEXT_STAGE_EXPERIMENT_GUIDE.md`
- `WORKSTREAM_B_REAL_AGENT_RISK_DATA_GUIDE.md`
- `docs/phase2/audit_inventory.md`
- `docs/phase2/risk_feature_audit.md`
- `docs/phase2/daily_reports/p1_d1_report.md`
- `docs/phase2/daily_reports/p1_d2_report.md`
- `docs/daily_reports/day_01_report.md` 至 `day_14_report.md`
- `docs/final_report.md`

### 2.2 工作树保护

审计开始及文档生成前，以下用户文件已经未跟踪，本审计不修改它们：

```text
?? WORKSTREAM_B_REAL_AGENT_RISK_DATA_GUIDE.md
?? docs/phase2/daily_reports/p1_d3_report.md
?? docs/phase2/utility_policy_audit.md
?? scripts/audit_p1_d3_utility_policy.py
?? tests/test_p1_d3_utility_policy_audit.py
```

未修改外部 Recovery-Bench，未覆盖 `runs/day01` 至 `runs/day14` 的历史产物。

## 3. 依赖版本证据

实际环境检查命令：

```bash
.venv/bin/python --version
.venv/bin/python -m pip show smolagents openai
rg -n 'smolagents|openai|httpx' pyproject.toml requirements.txt requirements.lock
```

结果：

```text
Python 3.12.3
smolagents 1.26.0
openai 3.52.0
httpx2 2.16.2  # requirements.lock
```

三个依赖声明文件均固定 `smolagents==1.26.0` 和 `openai==3.52.0`。本审计读取的
smolagents 源码来自：

```text
.venv/lib/python3.12/site-packages/smolagents/agents.py
.venv/lib/python3.12/site-packages/smolagents/memory.py
.venv/lib/python3.12/site-packages/smolagents/models.py
```

## 4. 当前真实 Agent 数据链路

```text
Day 7 catalog：40 templates / 200 instances
        │
        │ 选择 24 个 Train `_001` 实例
        ▼
Day 8：真实 Qwen3-8B ToolCallingAgent
        │
        ├── HTTPS → 远程 vLLM Worker 0/1
        ├── Tool Call → 本地 LocalBenchmarkTool
        ├── TaskToolRegistry → TaskEnvironment
        ├── ExecutionMonitor 旁路记录工具 Python 返回值/异常
        └── agent.run() 完成后读取 get_succinct_steps()
                 │
                 ├── 有真实模型执行的事后轨迹
                 └── 没有 Observation 后、下一轮模型前的 Checkpoint
                          ╳
                          ╳ 无完整 Agent State 可恢复
                          ╳
Day 8 logical_task_results.json
        │
        ▼
Day 9 build_intervention_scenario(position)
        │
        ├── 构造 fault_type / last_failure
        ├── 构造 synthetic tool_history
        ├── fresh pre-tool TaskEnvironment
        └── CheckpointManager schema v1
                 │
                 ▼
InterventionBranchRunner
controller_type=deterministic_local_reference
        │
        ├── CONTINUE：直接执行 pending tool
        ├── RETRY：直接执行 pending tool
        └── REPLAN：执行预定义 replan_steps
                 │
                 ▼
DeterministicVerifier
                 │
                 ▼
Day 10：20 条 Train 风险样本
```

这说明当前存在两条互不闭合的链：

```text
真实 Qwen 链：Qwen → 本地 Tool → Observation → 任务结束后日志
参考分支链：synthetic checkpoint → 本地确定性工具 → verifier
```

当前没有一条链满足：

```text
真实 Qwen → Agent-visible Observation → 完整 Checkpoint
          → 独立 Restore → Qwen CONTINUE → Verifier label
```

## 5. smolagents 真实执行循环源码证据

### 5.1 `run()` 与 memory 初始化

文件：`.venv/lib/python3.12/site-packages/smolagents/agents.py`

- `MultiStepAgent.run()`：约第 436 行；
- `run(reset=True)` 会调用 `memory.reset()`；
- 随后向 memory 添加一个新的 `TaskStep`；
- 非 stream 模式会完整消费 `_run_stream()`，调用方无法在中间状态暂停。

对应行为的核心源码结构：

```python
if reset:
    self.memory.reset()
    self.monitor.reset()
self.memory.steps.append(TaskStep(task=self.task, task_images=images))

if stream:
    return self._run_stream(...)

steps = list(self._run_stream(...))
```

### 5.2 ActionStep 生命周期

文件：`.venv/lib/python3.12/site-packages/smolagents/agents.py`，约第 540--604 行。

每轮执行顺序为：

1. 创建 `ActionStep`；
2. `_step_stream(action_step)` 进行模型调用和工具执行；
3. `_finalize_step(action_step)` 执行 callbacks；
4. `self.memory.steps.append(action_step)`；
5. `yield action_step`；
6. generator 被再次请求后才进入下一轮。

核心源码：

```python
finally:
    self._finalize_step(action_step)
    self.memory.steps.append(action_step)
    yield action_step
    self.step_number += 1
```

这意味着完成后的 `ActionStep` yield 是当前版本最清晰的捕获点：Observation 已经进入
memory，但调用方尚未请求下一轮 generator 输出，因此下一次模型调用尚未发生。

### 5.3 Tool Call 与 Observation 写入

文件：`.venv/lib/python3.12/site-packages/smolagents/agents.py`，约第 1215、1280、
1361--1442 行。

- `ToolCallingAgent._step_stream()` 先执行 `write_memory_to_messages()`；
- 模型返回值保存为 `memory_step.model_output_message`；
- `process_tool_calls()` 执行本地工具；
- 成功结果经 `str(tool_call_result).strip()` 转成 observation；
- 所有结果完成后写入 `memory_step.tool_calls` 和 `memory_step.observations`。

核心源码：

```python
memory_messages = self.write_memory_to_messages()
memory_step.model_input_messages = memory_messages.copy()
chat_message = self.model.generate(...)
memory_step.model_output_message = chat_message

tool_call_result = self.execute_tool_call(tool_name, tool_arguments)
observation = str(tool_call_result).strip()

memory_step.tool_calls = [...]
memory_step.observations += tool_output.observation + "\n"
```

### 5.4 `AgentMemory` 的消息转换

文件：`.venv/lib/python3.12/site-packages/smolagents/memory.py`。

- `ActionStep.to_messages()` 生成 Assistant、Tool Call 和 Tool Response 消息；
- `get_succinct_steps()` 会删除 `model_input_messages`；
- `get_full_steps()` 才保存完整 step dict；
- 当前版本没有 `AgentMemory.from_dict()`。

因此，保存 `get_succinct_steps()` 不足以重建下一轮模型输入；即使保存
`get_full_steps()`，也仍需要显式的反序列化/恢复适配器。

## 6. 实际生命周期探针及原始输出

审计使用当前 `.venv`、真实 `ToolCallingAgent` 和本地 CalculatorTool，模型为无网络
Probe Model。命令不会写入项目文件：

```bash
.venv/bin/python - <<'PY'
from smolagents import ToolCallingAgent
from smolagents.models import (
    Model, ChatMessage, ChatMessageToolCall,
    ChatMessageToolCallFunction, MessageRole,
)
from smolagents.memory import ActionStep
from risk_agent.tools.calculation_tools import CalculatorTool

class Probe(Model):
    def __init__(self):
        super().__init__(model_id='probe')
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content='',
            tool_calls=[ChatMessageToolCall(
                function=ChatMessageToolCallFunction(
                    name='calculator', arguments={'expression': '2 + 2'}
                ),
                id='probe-call-1',
                type='function',
            )],
        )

model = Probe()
agent = ToolCallingAgent(
    tools=[CalculatorTool()], model=model, max_steps=2, verbosity_level=0
)
stream = agent.run('Calculate 2 + 2.', stream=True)
for event in stream:
    if isinstance(event, ActionStep):
        print({
            'model_calls_at_capture': model.calls,
            'memory_step_types': [type(s).__name__ for s in agent.memory.steps],
            'action_in_memory': agent.memory.steps[-1] is event,
            'observation': event.observations,
            'message_roles': [m.role.value for m in agent.write_memory_to_messages()],
        })
        stream.close()
        break
PY
```

原始输出：

```text
{'model_calls_at_capture': 1,
 'memory_step_types': ['TaskStep', 'ActionStep'],
 'action_in_memory': True,
 'observation': '4',
 'message_roles': ['system', 'user', 'assistant', 'tool-call', 'tool-response']}
```

该证据支持使用 public streaming generator 的 `ActionStep` yield 作为捕获点。它不是远程
Qwen smoke，也不构成真实风险样本。

## 7. 当前项目代码逐项审计

### 7.1 Agent Factory

文件：`src/risk_agent/agent/agent_factory.py`

已具备：

- `OpenAIServerModel`；
- TLS CA bundle；
- `max_retries=0`；
- seed、temperature、max_tokens；
- 指定 Worker；
- `ToolCallingAgent` 与本地工具注册。

缺口：

- 没有 step streaming capture；
- 没有模型请求级 event/counter；
- 没有 Agent state export/import；
- 没有 Continue resume API。

### 7.2 Day 8 InitialTrajectoryRunner

文件：`src/risk_agent/collection/initial_trajectory.py`

关键源码位置：

```text
约 129 行：RuntimeState(... agent_memory=[{"role": "user", ...}])
约 183 行：agent.run(instruction, return_full_result=True)
约 196 行：agent.memory.get_succinct_steps()
```

结论：项目 `RuntimeState.agent_memory` 与 `agent.memory` 是两个不同状态容器。前者从未
在真实 Agent 执行期间同步 Assistant tool-call 或 Tool Response；后者只在任务结束后提取
succinct dict。

### 7.3 ExecutionMonitor

文件：`src/risk_agent/monitoring/execution_monitor.py`

`execute_tool()` 在本地 Python call 的 `finally` 中记录：

- arguments；
- Python result；
- exception type；
- token_usage 参数；
- latency；
- worker/environment/checkpoint 元数据。

但是 smolagents 随后会用：

```python
observation = str(tool_call_result).strip()
```

生成 Agent-visible Observation。因此 Monitor 保存的是 Observation 的来源对象，不是
Agent 下一轮真正收到的最终消息。异常路径还会被 smolagents 包装为
`AgentToolExecutionError`，Monitor 没有保存包装后的 Tool Response。

另外，Day 8 工具 Adapter 给 Monitor 的模型 Token 恒为：

```python
token_usage={"total_tokens": 0}
```

Checkpoint budget 因此没有包含截至决策时刻的真实模型 Token。

### 7.4 RuntimeState

文件：`src/risk_agent/monitoring/runtime_state.py`

`RuntimeState.agent_memory` 类型为：

```python
list[dict[str, Any]]
```

它没有 smolagents step 类型、ChatMessage、call ID 或框架恢复方法。现有测试只证明普通
JSON dict round-trip，不证明 `AgentMemory` round-trip。

### 7.5 CheckpointManager

文件：`src/risk_agent/checkpoint/manager.py`

Snapshot 实际保存：

```text
manifest.json
runtime_state.json
task_config.json
mock_api_state.json
files/**
database/api.sqlite3
```

`normalized_state_document()` 只包含：

```python
{
    "environment": environment.state_manifest(),
    "runtime": runtime_state.to_dict(),
    "task_config": task_config_dict(task),
}
```

Restore 返回 `RuntimeState`，没有返回或重建 `ToolCallingAgent`。因此 manifest 的
`state_hash` 不是完整 Agent state hash。

### 7.6 Reference Branch Runner

文件：

- `src/risk_agent/branching/runner.py`
- `src/risk_agent/intervention/runner.py`

`InterventionBranchRunner` 默认：

```python
controller_type = "deterministic_local_reference"
model_id = "local-reference-controller"
```

其 `_execute()` 对 CONTINUE/RETRY 直接读取 `pending_operation` 并调用本地工具；REPLAN
执行预先保存在 `replan_steps` 的 ToolStep。它没有创建 Agent，没有调用 vLLM，也没有让
Qwen 基于当前 Observation 生成下一步。

因此当前 CONTINUE 的语义是“执行控制器指定的 pending tool”，不是“恢复完整上下文后让
Qwen 自然继续”。

### 7.7 Fault Injector

文件：`src/risk_agent/faults/injector.py`

实际行为：

| Fault | 实现行为 |
|---|---|
| F0 | 正常执行 |
| F1 | 抛 `InjectedTimeoutError` |
| F2 | 抛 `InjectedServiceUnavailableError` |
| F3 | 抛 `InjectedInvalidArgumentError` |
| F4 | 返回配置的 stale result |
| F5 | 返回前一次结果 |
| F6 | 可先执行写操作，再抛 `PartialFailureError` |

代码搜索表明 `FaultInjector` 只在 `scripts/run_day4_faults.py` 和
`tests/test_faults.py` 中被调用；Day 8 `LocalBenchmarkTool` 没有接入它。因此当前真实
Qwen 轨迹没有配置化 F0--F6 注入。

若将当前异常直接接入，F6 异常文本含 `after commit` / `before commit`，可能向 Agent
泄漏内部 commit truth；正式路径必须增加 Agent-visible response adapter。

### 7.8 DeterministicVerifier

文件：`src/risk_agent/benchmark/verifier.py`

Verifier 检查：

- `result == expected["result"]`；
- 指定文件内容；
- orders 表指定字段；
- inventory 表指定字段。

它是确定性的，不使用 LLM Judge，适合继续复用。但它不判断：

- Qwen 是否正常给出 final answer；
- Agent 是否 `max_steps_error`；
- 失败是否来自模型服务、环境或任务；
- candidate result 是否来自合法终止路径。

正式标签需要在其外层增加 `ContinueOutcomeEvaluator`。

## 8. 历史产物直接证据

### 8.1 Day 8 真实模型产物

正式目录：

```text
runs/day08/day8_20261119_074032_7ec4c41e/
```

只读复核结果：

```text
24 logical tasks
43 append-only attempts
26 local tool calls
19 success
4 infra_failure
1 task_failure
20 token-complete attempts
input tokens sum = 68,685
output tokens sum = 1,648
total tokens sum = 70,333
mean total tokens = 3,516.7 per token-complete attempt
mean attempt latency = 10.86 seconds
median attempt latency = 5.33 seconds
run files = 51
run bytes = 1,131,593
```

结构抽样发现一个有工具调用的真实记录具有：

```text
agent_steps_n = 3
step types = TaskStep, ActionStep, ActionStep
first ActionStep has tool_calls = true
first ActionStep has observations = true
```

但这些 step 是任务完成后保存的 succinct steps，不是可恢复的运行中 Checkpoint。

### 8.2 Day 9 Checkpoint 产物

正式目录：

```text
runs/day09/day9_20261119_080843_288841fc/
```

抽样 `runtime_state.json`：

```text
runtime keys:
  agent_memory, budget, seed, tool_history, tool_state

agent_memory roles:
  system, user, controller

tool_state keys:
  last_failure, pending_operation, replan_steps,
  scenario_id, source_day8_task_run_id
```

没有 `assistant`、`tool-call` 或 `tool-response` role。P1-D2 对全部 20 个 runtime 的复核
同样发现 0 个真实 tool/observation role。

### 8.3 当前 catalog 的任务复杂度

对 Day 7 正式 `task_catalog.json` 的只读统计：

```text
200 / 200 tasks have exactly 1 reference step
150 tasks check result only
50 tasks check result + database
```

所以 Day 7 catalog 可复用作兼容/基础任务，但不满足 Protocol v2 提议的 3--8 步真实依赖链。

## 9. 四种执行模式的严格区分

| 模式 | 当前产物 | Qwen | 中间 Checkpoint | 模型驱动 Continue | 正式风险证据 |
|---|---|---:|---:|---:|---:|
| 真实 Qwen 执行 | Day 2、Day 8 | 是 | 否 | 否 | 否 |
| `deterministic_local_reference` | Day 3/6/7/9--13 | 否 | 项目自定义环境快照 | 否 | 否 |
| model-backed smoke | Day 2/8 smoke | 是 | 否 | 否 | 否 |
| formal model-backed experiment | 当前不存在 | 应为是 | 应为完整 | 应为是 | 尚无 |

未来数据必须至少记录：

```json
{
  "controller_type": "qwen3_model",
  "execution_mode": "model_backed_formal",
  "formal_experiment": true
}
```

Smoke 必须使用 `model_backed_smoke` 和 `formal_experiment=false`。

## 10. 当前远程 Worker/TLS/资源脚本复用审计

可复用：

- `RemoteModelSettings` 强制 HTTPS；
- CA bundle 和 API Key 由环境变量读取；
- OpenAI client `max_retries=0`；
- Worker 0/1 独立 endpoint；
- vLLM 只监听远端 loopback；
- SSH ControlMaster tunnel 使用 `ExitOnForwardFailure`；
- 状态文件、PID/cmdline 所有权检查；
- 健康检查和受控 TERM；
- GPU 两次采样和释放检查。

必须修正或增加外部 Gate：

1. `scripts/manage_remote_vllm.py` 的远端/local state 路径仍固定为 `day2`；
2. vLLM 固定 `--max-model-len 8192`；多步消息可能达到 context 上限；
3. `--max-num-seqs 1`，两个 Worker 总并发上限约为 2；
4. 脚本基于当前容器 `nvidia-smi` 判断空闲，不能发现 PID namespace 外的其他容器进程；
5. 正式运行前仍必须取得实验室级 GPU 使用确认；
6. 每次 run 必须记录检查时间、物理 GPU ID/UUID 与 Worker 映射。

本轮没有执行该脚本的 `status/start/stop/tunnel-*`。

## 11. 能力与缺口对照表

| 能力 | 当前实现 | 证据 | 缺口 | B0 判定 |
|---|---|---|---|---|
| 真实 Qwen Tool Call | 已有 | Day 2/8 产物 | 无中间恢复 | 部分具备 |
| Agent-visible Observation | 框架内存在 | smolagents ActionStep | 项目 Monitor 未精确持久化 | 不满足正式数据 |
| 决策时刻 hook | 框架可实现 | public stream probe | 项目未使用 | 可最小扩展 |
| 环境 snapshot | 已有 | CheckpointManager | 无 Agent state | 部分具备 |
| Agent snapshot | 无 | schema v1/manager | messages/steps/state 缺失 | 阻塞 |
| Agent restore | 无 | restore 仅返回 RuntimeState | 无 Agent 重建入口 | 阻塞 |
| Qwen CONTINUE | 无 | Reference runner 直接执行工具 | 非模型驱动 | 阻塞 |
| F0--F6 单测 | 已有 | Day 4/tests | 未接真实 Agent | 部分具备 |
| 确定性任务验证 | 已有 | DeterministicVerifier | 缺 outcome/infra 外层分类 | 可扩展 |
| 防泄漏风险特征 | 已审计 | P1-D2 | 真实 messages 特征未生成 | 阻塞正式训练 |
| 双 Worker/TLS | 已有 | Day 2/8 | 需新 run 资源审计 | 可复用 |

## 12. B0 Gate

### 12.1 B0 审计 Gate

**PASS**：已给出实际源码、依赖、历史产物和生命周期探针证据；已明确完整 Agent
Checkpoint 的缺口、最小技术路径、资源风险与下一阶段 Gate。

### 12.2 当前实现 Gate

**BLOCKED FOR FORMAL COLLECTION**：

- 当前有效真实 Agent Checkpoint：0；
- 当前真实 Qwen CONTINUE label：0；
- 当前 formal model-backed risk sample：0；
- Day 10 的 20 条样本不得计入上述数量。

### 12.3 解除阻塞的最低条件

1. schema v2 保存 Agent messages、full steps、runtime 和环境；
2. 捕获点满足 Observation 已进入 messages、下一模型调用未发生；
3. 至少 5 个真实 Qwen Checkpoint 5/5 Restore hash 一致；
4. 恢复后第一轮 Qwen 输入与 `next_model_input_hash` exact-match；
5. Continue 不添加干预 Prompt；
6. outcome/infra/verifier 三者正确分类；
7. 用户确认 B1 Protocol 后才能开始正式采集。
