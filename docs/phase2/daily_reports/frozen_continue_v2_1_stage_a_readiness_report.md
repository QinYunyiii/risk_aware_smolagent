# Frozen CONTINUE v2.1 — Stage A Readiness Report

日期：2026-11-27。基线 Commit：`5e1c10ed639678381eb02d5a7697391cd976c2e3`。

## Objectives

在新冻结配置下准备小规模 Train 的完整 Source → Checkpoint → 独立 Restore →
`resume(stream=True)` → Verifier 验收；先检查入口和离线端到端行为。
本轮没有启动真实 Qwen 验收或正式采集。

## Local Environment Work

初始工作区干净；已读取 AGENTS.md、PROJECT_GUIDE.md、README、Day 14 日报、
v2.1 冻结记录与日报，以及 Day5GateRunner、ContinueGateRunner 和 Replay Guard 实现。
未修改生产代码、配置、冻结 Manifest 或历史 Run。

独立审计目录：
`runs/phase2_continue_acceptance/p2_continue_stage_a_20261127_fcdb5465/`。

冻结校验仍为 `PROTOCOL_FREEZE_LOCAL_PASS`，Manifest SHA-256 仍为
`32e12bf71994919175e2639c6d3da04b529d56e49007486be42b6b9add22888c`。

## Remote Environment Work

真实 SSH、Tunnel、TLS 握手、HTTP/API、Qwen、GPU、vLLM、Docker 操作均为 0。
没有读取用户凭据。SDK 客户端使用 MockTransport 和临时 Fixture 凭据；
MockTransport 不验证真实 TLS，不证明真实模型服务可用。
GitHub 报告交付连接单独统计，不属于实验网络操作。

## Work Completed

发现两个接线差异和一个已复现的功能阻塞：

1. 原 B3 入口能完整 resume，但输出协议标识固定为 v2.0，不能直接当作 v2.1 入口。
2. 原 Day 5 `run_task()` 会另发一次受控恢复请求，不能直接拿它作为本轮 Source 生产者。
3. 成功的 Source 只读调用被无条件加入签名黑名单，导致后续合法的新查询被误判为重放。

前两项可通过新的薄接线解决；第三项必须先澄清并修复，不能靠修改 Prompt、
放宽 Verifier、跳过 API 任务或把历史失败重标来绕过。

## Offline End-to-End Reproduction

采用冻结 Catalog 中的 Train 任务 `p2_api_process_control_001`，加载原封不动的
1024-token 模型配置，实际驱动 smolagents、本地 SQLite 工具、CheckpointManager
及 ContinueGateRunner。只有模型返回由本地 MockTransport 预设。

| 阶段 | 直接观测结果 |
| --- | --- |
| Source | `get_order` 成功，订单 pending/version 1 |
| 捕获 | 第 1 次 Observation 后捕获，Source 仅 1 次 Mock 模型调用 |
| 独立 Restore | 六类 Hash 全匹配，首次恢复输入精确匹配 |
| CONTINUE 第一次返回 | `update_order` 成功，订单 processing/version 2；一个幂等写入 |
| CONTINUE 第二次返回 | 新调用 ID 的 `get_order`，参数为同一个完整订单号 |
| 工具执行 | 被 `CompletedToolReplayError: checkpoint_resume_replay_detected` 阻止 |
| B3 收尾 | 提前停止；termination=agent_error，Verifier=false，Mock y_continue=0 |
| 完整链路 | complete_chain=false，Checkpoint 未变，两个 Fixture 客户端均关闭 |

共 3 次 Mock Provider 调用：Source 1、CONTINUE 2；实际委托的 `max_tokens` 均为
1024。没有额外受控恢复请求，没有真实 Provider 调用。

该 Fixture 用 Day 5 既有元数据 helper 构造第一个决策点的 Checkpoint，再让原 B3
Runner 完整 resume，以定位冻结实现的兼容性。组件记录仍保留原 v2.0 标识；
外层审计明确注明正在检查 v2.1 的冻结依赖。这不是 v2.1 真实验收结果，
Mock 的 y_continue=0 不得进入训练集，也不代表真实 Qwen 在该任务上失败。

## Root Cause and Direct Evidence

- `src/risk_agent/collection/continue_gate.py:119`：成功工具调用先进入黑名单，
  之后才处理只读判断；只读例外仅覆盖失败调用。
- `src/risk_agent/collection/benchmark_tools.py:216`：使用“工具名＋参数”的签名
  判断，不考虑新调用 ID、读取时点或订单状态已被后续写入改变。
- `src/risk_agent/collection/continue_gate.py:537`：检测到该标记立即按 Agent Error
  结束流，后续不会得到正常 final_answer。

因此，读取同一资源的新 Observation 与重复执行 Source 已完成调用被混为一类。
这是已复现的客户端执行/Guard 问题；不归因于网络、TLS、远端 Worker 或模型质量。

为什么旧 Gate 没发现：P2-D2 验证到一次恢复模型请求为止，未执行返回的工具。
本轮用完整 resume 才实际走到“更新后再次查询”这一步。

## Minimal Repair Proposal — Not Applied

建议明确 Guard 的边界：

- Restore 不重新 dispatch 已完成的历史调用；历史调用 ID 保持不可重放。
- CONTINUE 新发起的只读查询属于新 Observation，允许读取变化后的状态；
  同名同参数本身不作为重放的充分依据。
- 已完成写入、已提交或提交状态未知的写入仍禁止盲目重放，幂等键和安全检查保留。
- 重复无进展仍受原模型/工具/时间预算和 F5 机制限制，不扩展 RETRY/REPLAN。

最小实现候选是在 CONTINUE 的 Source 签名集合构造和必要的调用身份校验处区分
只读新调用与历史重放，新增“更新后重读允许、历史调用不重派发、写入重放拒绝、
未知提交拒绝、失败只读可继续”的回归。不得简单关闭整个 Replay Guard。

冻结的 v2.1 文件和 Manifest 保留；实现修订需单独版本和可追溯的新 Manifest，
预算、Prompt、Catalog、Split、Verifier、标签、首个捕获点等研究定义保持不变。
本轮没有应用这项修复，也没有修改 TLS、连接模块或远端状态。

## Commands and Tests

本地 WSL：

```bash
.venv/bin/python scripts/verify_protocol_freeze.py
.venv/bin/python runs/phase2_continue_acceptance/p2_continue_stage_a_20261127_fcdb5465/offline_test_guard.py -q runs/phase2_continue_acceptance/p2_continue_stage_a_20261127_fcdb5465/test_full_continue_readiness.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_continue_acceptance/p2_continue_stage_a_20261127_fcdb5465/offline_test_guard.py -q tests/test_protocol_freeze.py tests/test_model_backed_checkpoint.py tests/agent/test_b3_continue_gate.py tests/agent/test_continue_outcome.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Test Results

- 离线端到端复现：1 passed，0.64 秒；该测试断言并证明现有阻塞，**不表示功能通过**。
- 相关既有回归：34 passed，0 failed，0 skipped，8.51 秒。
- 未重复全仓测试：本轮只新增文档，生产代码未变；上次全仓为 802 passed。
- compileall、diff 检查和报告敏感信息扫描通过。
- 7,874 个历史产物文件 Hash 保持原样，冻结校验继续通过。
- Guard 禁止实际网络和远端工具；仅依赖的 IPv6 支持检测 bind 被拒绝，无外部请求。

## Artifacts Generated

本地保留 baseline、复现脚本、Mock 请求正文、Checkpoint、分支工具/模型/Agent
事件、最终环境、Verifier 与结果、测试汇总、完整性检查和统一 Artifact Manifest。
原始材料不上传 GitHub；Git 仅保存本脱敏报告。

## Known Issues / Remaining Work

`FROZEN_CONTINUE_STAGE_A_BLOCKED`：完整真实验收尚不具备执行条件。
先确认上述 Guard 边界与实现修订，再进行最小修复及离线回归，最后准备真实验收。
本轮未生成新的真实结果，未改变上次 Protocol Freeze 的本地 PASS，未启动正式采集。

需要用户确认的依据是 AGENTS.md 第 4 节：
“以下内容修改前必须说明原因并取得用户确认”，其中包括动作语义和
Checkpoint / Restore 协议。只读新调用与重放的边界已进入冻结要求，不能静默改写。

## Status: BLOCKED

等待审核 Guard 修订范围；不执行真实连接或实验。

## Git Commit

按 AGENTS.md 第 15 节只交付本报告；提交 SHA 在最终回复和本地交付记录中保存，
不嵌入本文件造成自引用。
