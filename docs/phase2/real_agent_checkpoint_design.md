# 完整 Agent Checkpoint 最小设计方案

> 文档状态：B0 设计草案，尚未冻结\
> 日期：2026-11-19（Asia/Shanghai）\
> 适用依赖：`smolagents==1.26.0`\
> 前置条件：用户确认 B1 Protocol 后才能实施\
> 目标：恢复后第一轮 Qwen 输入与捕获时的下一轮输入完全一致

## 1. 设计结论

最小可行方案不是重写现有 `CheckpointManager`，而是在其环境快照能力上增加一个
Agent State Adapter 和一个可恢复的 ToolCallingAgent wrapper：

```text
现有 CheckpointManager
  ├── files
  ├── SQLite
  ├── Mock API
  ├── RuntimeState
  └── TaskSpec
          +
AgentStateAdapter
  ├── canonical messages
  ├── full memory steps
  ├── current Agent runtime
  ├── model/prompt/tool identity
  └── next-model-input hash
          +
CheckpointableToolCallingAgent
  └── resume without adding a new TaskStep or intervention prompt
```

不修改 `.venv/site-packages/smolagents`，不 monkey-patch 已安装包。实现使用公开 stream
接口捕获；恢复若必须调用版本相关的 protected API，则只放在一个受控 subclass 中，并用
依赖版本断言和兼容测试保护。

## 2. 设计目标与非目标

### 2.1 必须达到

- 决策时刻是 Observation 已进入 Agent 上下文、下一轮模型调用尚未发生；
- 完整保存下一轮推理所需 messages；
- 保存可审计的 smolagents full steps；
- 保存 Agent、Runtime、Environment、Budget 和工具状态；
- 从两个独立 workspace Restore 得到相同初始状态；
- 恢复后真实 Qwen 能自然 CONTINUE；
- 第一轮恢复输入与捕获的下一轮输入 canonical hash 一致；
- 保持 schema v1 历史 Checkpoint 可读；
- 不保存密钥、客户端连接、TLS 私钥或不可序列化网络对象。

### 2.2 不要求

- 不保存 Python 进程内存或 CRIU；
- 不保存活动 HTTP/TCP 连接；
- 不序列化 OpenAI client；Restore 时从安全配置重建；
- 不保证 Qwen 输出确定性，只保证输入状态和 generation config 可追溯；
- B2 只完成 CONTINUE 所需状态，不提前实现 RETRY/REPLAN 效用分支。

## 3. 决策时刻

固定定义：

> 完成一个非 final-answer 的 `ActionStep` 后，工具结果或错误反馈已经通过
> `ActionStep.to_messages()` 进入 `agent.write_memory_to_messages()`，但 stream generator
> 尚未被请求下一项，因此下一次 `model.generate()` 尚未发生。

### 3.1 捕获条件

候选 `ActionStep` 必须满足：

- `agent.memory.steps[-1] is event`；
- `event.is_final_answer is False`；
- `event.observations is not None` 或 `event.error is not None`；
- messages 最后一项是 Tool Response；
- step 已有模型 token usage，或明确记录不可观测原因；
- 当前任务尚有剩余 step/tool/token budget；
- decision trigger 符合 B1 预注册规则。

### 3.2 为什么不使用 callback

当前 smolagents `_finalize_step()` 在把 `ActionStep` 加入 `agent.memory.steps` 之前调用
callback。callback 收到的 `memory_step` 已有 Observation，但 `agent.memory` 尚未包含它。
使用 callback 需要手工拼接当前 step，容易与下一轮真实 message conversion 偏离。

public stream 在 `memory.steps.append(action_step)` 后 yield，时序更直接且已通过 B0 探针验证。

## 4. Checkpoint schema v2

### 4.1 目录结构

```text
checkpoint_<id>/
├── manifest.json
├── agent/
│   ├── messages.json
│   ├── memory_steps.json
│   ├── runtime.json
│   ├── latest_action_step.json
│   └── tool_runtime.json
├── runtime_state.json
├── task_config.json
├── mock_api_state.json
├── files/**
└── database/api.sqlite3
```

### 4.2 `agent/messages.json`

来源必须是：

```python
agent.write_memory_to_messages()
```

每条 `ChatMessage` 使用不含 `raw` 对象的 canonical JSON：

```json
{
  "role": "tool-response",
  "content": [
    {"type": "text", "text": "Observation:\n..."}
  ],
  "tool_calls": null
}
```

该文件是恢复后第一轮模型输入的权威来源。保存前后计算
`next_model_input_hash`。

### 4.3 `agent/memory_steps.json`

来源：

```python
agent.memory.get_full_steps()
```

用途：

- 审计 TaskStep/ActionStep/PlanningStep；
- 保留 Assistant output、tool call、Observation、error 和 token usage；
- 复核 messages 是否可由 step history 重新生成；
- 不作为唯一恢复输入，因为当前 `AgentMemory` 没有公开 `from_dict()`。

### 4.4 `agent/runtime.json`

建议字段：

```json
{
  "schema_version": "2.0",
  "framework": "smolagents",
  "framework_version": "1.26.0",
  "agent_class": "ToolCallingAgent",
  "task": "original instruction",
  "agent_name": "...",
  "agent_state": {},
  "completed_step_number": 2,
  "next_step_number": 3,
  "max_steps": 8,
  "remaining_steps": 6,
  "stream_outputs": false,
  "planning_interval": null,
  "system_prompt_sha256": "...",
  "prompt_template_sha256": "...",
  "tool_schema_sha256": "...",
  "model_id": "Qwen/Qwen3-8B",
  "model_version": "...",
  "worker_id": "worker-0",
  "generation_config": {
    "seed": 20261118,
    "temperature": 0.0,
    "max_tokens": 256
  },
  "model_calls_completed": 2,
  "prompt_tokens_used": 0,
  "completion_tokens_used": 0
}
```

真实 Token 不可观测时写 `null`，不能用 0 代替。

### 4.5 `agent/latest_action_step.json`

至少保存：

- `step_number`；
- Assistant model output message；
- tool call name/arguments/call ID；
- exact `ActionStep.observations`；
- exact error response；
- token usage；
- timing；
- Agent-visible Tool Response digest；
- 对应 Monitor tool event ID。

### 4.6 `agent/tool_runtime.json`

至少保存：

- LocalBenchmarkTool step counter；
- FaultInjector 每工具 call index；
- FaultInjector RNG state 或可确定重建的 seed + 已消费计数；
- F5 所需 `_last_results`；
- 当前幂等键集合/写入状态；
- 最新工具调用的 internal event reference；
- 不把内部 fault truth复制到 `messages.json`。

### 4.7 manifest v2

建议新增：

```json
{
  "schema_version": 2,
  "checkpoint_id": "...",
  "agent_state_hash": "...",
  "next_model_input_hash": "...",
  "environment_hash": "...",
  "runtime_hash": "...",
  "task_config_hash": "...",
  "tool_runtime_hash": "...",
  "model_config_hash": "...",
  "combined_state_hash": "...",
  "artifact_checksums": {}
}
```

组合 hash 的 canonical document：

```text
H({
  agent_state,
  next_model_input,
  environment,
  runtime_state,
  task_config,
  tool_runtime,
  model_prompt_tool_identity
})
```

## 5. Observation 精确性

### 5.1 当前差异

当前 `ExecutionMonitor` 记录工具 Python 返回对象；smolagents 再将其转换为：

```python
str(tool_call_result).strip()
```

异常还会被包装为 `AgentToolExecutionError`。因此正式 Checkpoint 不能只使用
`RuntimeState.tool_history[-1].tool_result` 代替 Agent-visible Observation。

### 5.2 双层记录

必须同时保留：

```text
raw_tool_event
  ├── tool arguments
  ├── Python result / exception
  ├── latency
  └── internal fault event reference

agent_visible_event
  ├── Assistant tool-call + call ID
  ├── ActionStep observation/error
  ├── Tool Response message
  └── next-message-list hash
```

风险特征只能从 `agent_visible_event` 或其他决策时刻允许字段提取。

## 6. Fault 响应设计

正式 Agent 工具 Adapter 应将内部故障真值与可见响应分离。

| Fault | Agent-visible 内容 | 内部审计内容 |
|---|---|---|
| F0 | 正常结果 | no-fault schedule |
| F1 | timeout / no response | actual commit status |
| F2 | service unavailable response | injected F2, phase, seed |
| F3 | argument validation response | injected F3, expected schema |
| F4 | stale data/version conflict | true current version |
| F5 | repeated/old result或无进展现象 | injector call count |
| F6 | response lost / outcome uncertain | committed/not_committed/unknown truth |

F6 的内部 `committed` 不应直接出现在 Agent-visible 文本。安全策略可以通过状态查询发现
结果，但风险特征不能读取隐藏 truth。

## 7. 捕获算法

伪代码：

```python
agent, model = create_checkpointable_agent(...)
stream = agent.run(instruction, stream=True)

for event in stream:
    record_stream_event(event)

    if not should_checkpoint(event):
        continue

    assert isinstance(event, ActionStep)
    assert agent.memory.steps[-1] is event

    next_messages = agent.write_memory_to_messages()
    assert next_messages[-1].role == MessageRole.TOOL_RESPONSE

    calls_before = model_monitor.completed_calls
    checkpoint = checkpoint_manager.snapshot_v2(
        environment=environment,
        runtime_state=runtime_state,
        agent_state=agent_state_adapter.capture(agent, event),
        tool_runtime=tool_runtime.capture(),
    )
    assert model_monitor.completed_calls == calls_before

    yield checkpoint
    # 下一次迭代之前模型不会继续调用
```

捕获过程必须同步完成。不得把 snapshot 放入后台线程后立即继续 generator。

## 8. Restore 算法

```text
1. 选择 checkpoint_id 和 branch_id
2. 创建唯一 branch root
3. 校验所有 artifact checksum
4. Restore files/SQLite/Mock API
5. Restore RuntimeState/Budget
6. Restore tool/fault runtime
7. 重建同版本 tools，绑定到新 TaskEnvironment
8. 从安全配置重建同一 Worker 的 OpenAIServerModel
9. 重建 CheckpointableToolCallingAgent
10. 加载 canonical message prefix 和 Agent runtime
11. 重算 agent/environment/combined hashes
12. 检查 first model input hash == next_model_input_hash
13. 只执行 remaining_steps
14. 输出完整 branch events、usage、termination 和 verifier result
```

### 8.1 不得使用的恢复方式

- `agent.run(original_task, reset=True)`：会清空 memory；
- `agent.run(original_task, reset=False)`：会增加新的 TaskStep；
- 只把 message 文本拼进一个新 User Prompt：会改变 role/tool-call 结构；
- 只恢复 environment 后重新从任务开头运行：不是同一 Checkpoint；
- 直接恢复 Day 9 `RuntimeState.agent_memory`：不是 smolagents memory。

## 9. CONTINUE 语义

CONTINUE 固定为：

- 不添加任何干预 Prompt；
- 不重复已经完成的工具调用；
- 恢复后第一项待办是下一次模型生成；
- 使用原 Worker、模型、Prompt、工具和 generation config；
- 使用原剩余预算；
- 模型基于已保存的 Tool Response 自然决定下一步；
- 工具调用继续在本地 Restore workspace 执行。

注意：决策时刻已经位于工具响应之后，所以 `pending_operation` 应表示：

```json
{"kind": "model_generation", "next_step_number": 3}
```

上一工具调用应保存为 `latest_completed_operation`，不能继续沿用 Day 9 的“pre-tool
pending operation”含义。

## 10. 结果与标签

建议由新 `ContinueOutcomeEvaluator` 组合三类证据：

1. Agent/模型终止状态；
2. 基础设施与环境失败分类；
3. `DeterministicVerifier` 结果。

```text
normal final + verifier pass       -> Y_continue = 1
normal task failure/verifier fail  -> Y_continue = 0
max steps                          -> 0（需 B1 明确确认）
model service failure              -> null
unexpected environment failure     -> null
operator cancel                    -> null，除非 B1 预注册为预算失败
```

Checkpoint 本身不保存标签。标签在 Continue branch 完成后通过 `checkpoint_id` 派生 join。

## 11. 向后兼容

`CheckpointManifest.from_dict()` 当前只接受 schema version 1。实施时建议：

- 保留现有 v1 dataclass/reader；
- 新增 v2 manifest 类型或显式 union；
- v1 restore 行为不变；
- v1 不得被自动升级为“完整 Agent Checkpoint”；
- 只有 v2 且 Agent payload/hash 全部通过，才能设置
  `agent_context_restore_supported=true`；
- Day 5/6/9 测试继续运行。

## 12. 测试设计

### 12.1 单元测试

- ChatMessage canonical round-trip；
- TaskStep/ActionStep/PlanningStep audit serialization；
- Assistant tool-call/call ID/Observation 保留；
- fault response 与 internal metadata 分离；
- Token `null` 与真实 0 区分；
- v1/v2 manifest dispatch；
- artifact corruption 拒绝；
- prompt/tool/model hash 不匹配拒绝；
- remaining budget restore；
- `next_model_input_hash` exact-match。

### 12.2 生命周期测试

使用 spy model：

- 第一次模型返回 Tool Call；
- 工具返回 Observation；
- ActionStep yield 时 model call count 仍为 1；
- snapshot 后仍为 1；
- Restore CONTINUE 后才变为 2；
- 第二次模型实际收到的 messages 与 snapshot 完全相同。

### 12.3 环境集成测试

- 文件修改后 Restore exact-match；
- SQLite backup/restore exact-match；
- 幂等键表 exact-match；
- 两个 Restore workspace inode 不同；
- 分支 A 写入不影响分支 B；
- Agent message hash 相同而 workspace 独立。

### 12.4 真实 Qwen Gate

至少 5 个真实任务：

- 5/5 Checkpoint 有真实 Assistant tool-call 和 Tool Response；
- 5/5 Restore agent/environment/combined hash 匹配；
- 5/5 第一轮恢复输入 hash 匹配；
- 5/5 恢复后发出真实 Qwen 请求；
- infra failure 不产生伪标签；
- Token/Latency 可观测，缺失写 null；
- 原始消息完成敏感信息扫描。

任何一项失败，B2 状态必须为 BLOCKED。

## 13. 预计代码范围

### 修改

- `src/risk_agent/agent/agent_factory.py`
- `src/risk_agent/collection/benchmark_tools.py`
- `src/risk_agent/monitoring/runtime_state.py`
- `src/risk_agent/monitoring/execution_monitor.py`
- `src/risk_agent/checkpoint/schema.py`
- `src/risk_agent/checkpoint/manager.py`

### 新增

- `src/risk_agent/agent/agent_state_adapter.py`
- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/collection/decision_checkpoint.py`
- `src/risk_agent/faults/agent_response_adapter.py`
- `src/risk_agent/branching/model_backed_continue.py`
- `src/risk_agent/evaluation/continue_outcome.py`
- 对应专项测试。

## 14. B1 必须确认的设计项

实施前需用户确认：

1. ActionStep stream yield 是否正式冻结为决策时刻；
2. max-steps 是否正式记为风险负类；
3. operator/budget stop 的标签规则；
4. F1/F6 Agent-visible response 文案和隐藏 commit truth 边界；
5. 是否只允许单工具调用 ActionStep 进入第一版数据；
6. 最大步骤、8K context 溢出和消息压缩协议；
7. 5 个真实 Checkpoint Gate 后是否先实施 B3，再进入 B4 smoke。

在这些事项确认前，本文件只是实现设计，不构成冻结协议。
