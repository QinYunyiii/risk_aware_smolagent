# CONTINUE v2.2：输出契约与工程验收入口

本修订落实 2026-11-27 用户对输出契约/Gate 审查方案的明确批准。
协议 ID 为 risk-continue-v2.2.0，implementation_revision 为 output-contract-v1，
入口为 frozen-continue-acceptance-v2；Checkpoint Schema 保持 2.0.0。
本地实现通过不授权真实模型实验或正式采集。

## 相对 v2.1 的变更

1. 所有 160 个任务追加同一段公开输出说明，要求 final_answer.answer 保持原生
   JSON 类型、完整字段及精确文本。提示渲染器仅接收公开 prompt 和固定契约文本，
   不读取 expected、reference_steps、故障标签或未来结果。
2. output_contract 模块的 CheckpointableToolCallingAgent 复用原工具执行、预算和 Replay Guard，只把非 final_answer
   ToolOutput.observation 编码为 UTF-8 JSON。原 output、工具监控记录及工具实现
   不变；字符串首尾空白、换行、Unicode 和数字外观字符串保留。NaN、非字符串
   对象键、tuple、循环等无法无损表达的结果 fail-closed，不能退回 str。
3. engineering_passed 表示有效 0/1 结果及审计链路通过，task_success 单独表示
   accepted_y_continue 是否为 1。有效 0 继续下一固定任务；null、状态/输入/
   Replay/审计/客户端释放异常停止。五任务全部完成且最终连接清理通过才发布工程 PASS。

原 Verifier、0/1/null 含义、任务数据/参考答案、Split、T1–T5 与 Worker 安排、
Seed/Temperature/1024 Completion、总预算、首个合格捕获规则和只读重读策略不变。
成功率字段 task_success_rate_among_valid 的分母为有效 0/1 数；另报 censored_or_invalid
和 not_attempted，不把中断当作模型失败。不要求五条全部成功或强行产生某个标签比例。

## 版本和恢复绑定

统一清单为 docs/phase2/protocol_freeze_manifest_v2_2.json；它固定新配置、实现、
契约文本和本说明，并绑定不变的 v2.1 父清单与入口清单。加载时先验证完整父链。
旧清单、旧入口及旧实验结果原样保留，不刷新旧清单来掩盖源码变化。

新 RemoteModelSettings 只更改 prompt_version；模型推理参数不变。
新的有效 Catalog Hash 包含追加后的公开 Prompt，Split Hash 与父版本相同。
Source 从运行前即使用新 Agent 类和新提示；Snapshot 保存这个类、Prompt、
完整 Observation 及实际下一输入。Restore 用相同类和配置，核对 Prompt Version、
Catalog、Split、六类 Hash、首次实际输入和 Replay Guard，不做恢复后临时补丁。
新旧 Checkpoint 不交叉解释；保留旧入口用于旧版本恢复。

## 本地入口

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance_v2.py --mode local-preflight
.venv/bin/python scripts/run_frozen_continue_acceptance_v2.py --mode offline-smoke
```

默认本地预检；离线模式禁止真实网络，使用明确标记的 Mock SDK。
Mock 的参考步骤只供离线测试使用，其成功不证明真实模型成功。
私有产物使用新的唯一 Run ID，默认在 runs/phase2_continue_acceptance_v2；原始
Source/Checkpoint/Provider/Parsing/Verifier/清理审计及 Manifest 均保留。
run_manifest 记录实际 Git HEAD、脏工作区标志、协议/入口身份、Catalog/Split 和清单 Hash。

真实入口的参数形状仍为：

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance_v2.py \
  --mode real-five-task-gate --authorize-remote --run-id <新唯一ID> \
  --remote-evidence <新鲜且绑定本次Run的私有证据.json> \
  --connection-spec <私有连接配置.json>
```

该示例不是执行授权。真实运行须用户单独批准，并重新核验现有 Worker、SSH 身份、
模型/权重/GPU 映射和严格 TLS/鉴权；不启动或重启 vLLM，不分配新 GPU，不自动重试。
本次批准仅用于本地修订实施。

## Gate 与产物

- 本地预检：CONTINUE_ENTRY_V2_PREFLIGHT_PASS。
- 离线 Mock 全闭环：CONTINUE_ENTRY_V2_OFFLINE_PASS。
- 获准真实五任务工程验收：CONTINUE_ENGINEERING_GATE_PASS。
- 任一工程门禁失败：CONTINUE_ENGINEERING_GATE_BLOCKED。

passed 与 engineering_passed 均是工程状态，不代表所有任务成功。
任务成功数 successes、有效失败数 valid_failures 和每任务 task_success 单独保留。
客户端释放失败使任务标签不被接纳；最终隧道释放失败阻塞最终 Gate，原始 Verifier
观测结果仍保留，不能因此篡改成 0。

gate_summary_final_audit.json 在最终连接清理后发布，为权威结果；artifact_manifest.json
发布文件长度及 SHA-256，清单不包含自身。正式标记和 research_dataset_eligible 始终
为 false，formal_collection_ready 为 false，等待单独实验审核。
