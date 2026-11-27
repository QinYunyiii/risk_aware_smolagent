# CONTINUE Replay Guard — readonly-refresh-v1

日期：2026-11-27。用户已批准修复“新发起的只读查询被误判为 Source 重放”。
研究协议仍为 `risk-continue-v2.1.0`，Checkpoint Schema 仍为 `2.0.0`；
本文件记录独立的实现修订 `readonly-refresh-v1`。

## 边界

- 通过 `resume(stream=True)` 从捕获后的下一步继续，不重新 dispatch 历史 Memory。
- 新发起的只读调用使用新 Tool Call ID，即使工具名和参数与 Source 一样，也允许
  执行并生成新的 Observation。订单更新后重新查询同一订单属于这种情况。
- 历史 Tool Call ID、同一新批次的重复 ID、CONTINUE 已使用的 ID 一律拒绝，
  在执行本批任何工具之前停止。只读和写入均受 ID 检查。
- 已完成写入仍按签名禁止重放；失败写入沿用既有提交证据规则：committed、
  unknown 或缺失证据禁止重放；明确 not_started/not_committed 的既有安全规则保留。
- 不按“有幂等键”就绕过 Guard，不自动重试、不切换 Worker，不扩大动作集。
- 模型/工具/Token/时间预算及 F5 故障机制保留，允许新读不代表无限调用。

## 实现和启用

`ContinueGateRunner` 默认仍使用 `historical-signatures-v1`，旧入口行为保留。
新配置 `configs/phase2_risk_collection_v2_1_readonly_refresh.yaml` 显式选择
`new-readonly-observations-v1`。修订同时使用写入签名保护和调用身份保护，
不能只取消只读签名拦截而不启用 ID 检查。

新的 `ContinueGateRunner.from_frozen_collection(...)` 在构造模型客户端之前
校验冻结清单并加载该配置，再绑定其策略、模型配置、有效任务和协议 ID。
它复用既有完整 CONTINUE 与 Verifier 路径，不创建另一套模型执行框架。

ID 集合从已经恢复的 Memory 中生成，不写入 Checkpoint，不改变六类 Hash。
恢复后的新请求仍须精确匹配输入；工具执行前的检查与预算计数继续生效。
被 ID Guard 拒绝的合法解析响应仍记录 Parsing Outcome，避免审计链出现空洞。

## 冻结和历史兼容性

父 Manifest：`docs/phase2/protocol_freeze_manifest_v2_1.json`，SHA-256：
`32e12bf71994919175e2639c6d3da04b529d56e49007486be42b6b9add22888c`。
它及父配置保持原样。新清单为
`docs/phase2/protocol_freeze_manifest_v2_1_readonly_refresh.json`，记录父清单 Hash、
原研究输入和修订实现 Hash。运行时没有自动更新或忽略 Hash 的后门。

默认离线校验当前修订：

```bash
.venv/bin/python scripts/verify_protocol_freeze.py
```

显式检查旧实现：

```bash
.venv/bin/python scripts/verify_protocol_freeze.py --revision original-v2.1
```

当前工作树的实现已修订，旧清单应严格报告源码漂移。要复现旧实现，使用
`5e1c10ed639678381eb02d5a7697391cd976c2e3` 的独立只读检出；不得刷新旧清单
让新代码冒充旧实现。历史 Run、原先的 BLOCKED 结果和 Mock 证据均不重标。

预算、Prompt、Catalog、Split、Reference/expected、故障计划、Verifier、标签、
首个合格捕获点、每任务一个 primary Checkpoint 和本地/远端职责均未改变。

## 验收与限制

离线用例必须完整执行：首个查询后捕获、独立 Restore、六类 Hash、精确输入、
更新、同参数新 ID 查询、final_answer、确定性 Verifier；并检查仅一次写入、
幂等记录、持续预算、Checkpoint 不变、Source 环境不变和客户端释放。
负例覆盖历史 ID、CONTINUE ID、重复批次 ID、写入重放和未知提交状态。

本地 Mock 成功不代表真实 Qwen 通过，产物保持非正式、不可用于训练。
正式采集就绪状态仍为 false；Source 生产入口的完整接线与真实验收仍须另行完成。
本轮不运行 SSH、TLS/API、Qwen、GPU/vLLM 或正式采集。
