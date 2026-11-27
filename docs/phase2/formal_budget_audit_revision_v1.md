# Budget Boundary Audit v1

这是正式生产者的独立实现修订，研究语义仍为 risk-continue-v2.2.0。
原 formal_collection_manifest_v1.json、父协议全部文件及历史 Run 保持不变。
新清单 formal_budget_audit_manifest_v1.json 通过父清单 Hash 绑定旧实现，固定新增文件。

新 Agent 保留原公开类名（Checkpoint v2 白名单要求），使用不同模块路径。
Source 和独立 Restore 均由 BudgetAuditFormalCollector 的工厂构造此 Agent。
Provider 工厂、工具执行、Prompt、预算、重放保护和 Verifier 沿用原生产者。
新 Run 的 implementation_binding.json 和 frozen_revision_inputs 在执行前保存新版本；
记录追加 implementation_revision/implementation_manifest_sha256，不覆盖旧身份文件。

唯一行为差异是审计：若 ModelRequestRejected 在任何 ToolCall yield 之前被原前置检查
包装为 AgentToolExecutionError，则为该确切模型响应记录已有 parsing_status=not_attempted，
reason=tool_execution_preflight_blocked:<原保护原因>，随后重新抛出原异常。
不伪造解析成功，不延后预算检查，不执行被阻止的工具，不额外发出模型请求。
普通成功、真实解析失败、历史 ID 重放和产生 ToolCall 后的工具错误仍由原路径记录，
避免同一响应重复写收据；审计持久化失败继续 fail-closed。

可观测预算耗尽且审计完整时，确定性 Verifier 仍按原协议决定 0/1；Usage/审计缺失仍为 null。
这只适用于新版本的新运行，不对历史预算耗尽的空标签重新计算或补写。

prepare_formal_train_continuation.py 只有离线入口，强制网络审计钩子，验证父 Run 原始
Manifest 的可信 Hash、所有文件、journal 和任务前缀，生成待审核续跑计划。
它排除所有已经尝试的任务（包括空标签），保持剩余任务的原顺序、Worker 和重复分配。
--offline-gate 使用非正式 Fake SDK 在新目录完整执行 96+19，不能产生正式样本或授权真实采集。

真实续跑方案必须显式绑定新 child Run 与只读 parent Run；保留旧 BLOCKED 和 null，
不调用旧 --resume 绕过停批，不改旧 Manifest，不再次提交任何已尝试任务。
整批合并审计需验证 79+17 个唯一 Source attempts、13+6 次预选重复，以及新旧版本
边界和完整故障覆盖。最多可能获得 95 个有效主标签，不能保证或补齐到 96。
实际执行仍需审核此精确计划、新鲜 Worker/模型/TLS/归属证明和独立的真实入口验收。
本轮离线修复不自动授权 SSH、API、Validation/Test、训练或数据集发布。
