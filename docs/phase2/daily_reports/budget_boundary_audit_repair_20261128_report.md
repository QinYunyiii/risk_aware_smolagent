# Budget Boundary Parsing Audit Repair — 2026-11-28

## Objectives

修复正式 Train 第 79 项触达预算门禁时缺少 Parsing 收据的边界问题，完成离线回归和
保留历史证据的续跑方案。本轮不发起 SSH、HTTP、Qwen 请求或真实采集。

原正式 Run `formal_train_20261127_ad34d6d2` 始终保持 `TRAIN_COLLECTION_BLOCKED`：
78 个有效主样本（72 成功、6 失败）、13 个有效重复、1 个空标签。历史标签不重算。

## Root Cause and Minimal Change

原 Agent 在上游首次 yield ToolCall 时才记录 Parsing；模型调用预算、工具调用预算和
响应成本门禁可能先于该 yield 阻止执行，所以真实响应已返回但没有 Parsing 配对。
冻结实现的定位为 `checkpointable_tool_agent.py:98` 和 `:165–170`。

新增 `budget_boundary_audit.CheckpointableToolCallingAgent`，继承固定的 lossless Agent。
若在任何 ToolCall yield 之前，原前置检查以 ModelRequestRejected 的原因拒绝执行，
为该确切 ChatMessage 的 Audit ID 记录已有字段：

```text
parsing_status = not_attempted
reason = tool_execution_preflight_blocked:<原保护原因>
```

随后重新抛出原异常。没有伪造 parsing success，没有调整/移除预算检查，没有执行被阻止
的工具，没有增加 Provider 请求。普通成功、真实解析失败及重放保护仍由原路径记录；
发生过 yield 后不补第二条收据，持久化失败继续保留 null 并停批。

新旧 Fake 端到端对照复现同一失败路径：10 个相同 SDK 请求（Source 1 + CONTINUE 9）、
8 次相同分支工具执行；两边请求正文逐项相等。新版本记录 9 个成功 Parsing + 1 个
not_attempted，原 Verifier 规则将可观测预算耗尽判为 0。旧版本保持 9 条 Parsing 和 null。
这是两个新的非正式 Fixture 的对照，不是对历史真实 Run 重新分类。

## Implementation and Freeze

- 新生产类：`BudgetAuditFormalCollector`，Source 和独立 Restore 都构造新版本 Agent。
  未替换 Checkpoint/Restore 算法、故障注入器、特征、模型客户端、CONTINUE 流或 Verifier。
- 使用独立 `implementation_binding.json` 和 `frozen_revision_inputs`，在请求发生前绑定
  新实现。新旧公共类名相同以满足原 Checkpoint v2 白名单，完整模块身份不同。
- 旧生产者、旧正式入口、旧 Manifest 及父协议所有文件不变；禁止把旧 Run 通过
  `resume=True` 直接放入新实现，也不修改历史 Run 的身份文件。
- 新清单 `formal_budget_audit_manifest_v1.json`，修订 `budget-boundary-audit-v1`，SHA-256：
  `86374f8ba1e3770cc4a0d45544c1ca0ac6fd21aba5f66e09e4a1cbb20b9f4951`。
- 新清单绑定原正式 Manifest
  `553ce3c15b34588889775f7c95cadd5ece94219e1f45b18b165e4858a1c82d08`，固定 4 个新增实现/入口/说明文件。

未改变任务、Split、Prompt、Verifier、Seed、Temperature、1024 Completion Tokens、预算、
故障排程、捕获规则或标签语义。没有补写旧 Parsing 收据、补造 Verifier、回改空标签。

## Commands and Tests

本地 WSL `.venv`，测试审计钩子禁止真实网络和远程工具。主要实际命令：

```bash
.venv/bin/python runs/phase2_budget_audit_repair/budget_audit_repair_20261128_3b529b72/offline_test_guard.py tests/test_budget_boundary_audit.py -q
.venv/bin/python runs/phase2_budget_audit_repair/budget_audit_repair_20261128_3b529b72/offline_test_guard.py -q
.venv/bin/python scripts/prepare_formal_train_continuation.py --source-run runs/phase2_formal_train/formal_train_20261127_ad34d6d2 --source-manifest-sha256 8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00 --output runs/phase2_budget_audit_repair/budget_audit_repair_20261128_3b529b72/qualification --run-id budget_audit_offline_20261128_3b529b72 --offline-gate
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

- 首轮单独专项 12 passed；之后新增“历史调用 ID 重放”和“最后一次模型调用允许 final_answer”
  两项边界测试，共 14 项，包含真实 Source 捕获/独立 Restore/完整 CONTINUE 的 Fake 测试。
- 覆盖模型预算、超大工具 batch 原子拒绝、SDK 前拒绝无假 Provider 收据、正常/失败解析不
  重复记账、缺失 Usage、服务故障、审计磁盘故障不变为 0，以及历史漂移/旧版本 resume 拒绝。
- 完整新版本离线入口：96 个有效主分支 + 19 个有效重复，质量及故障覆盖通过，5241 个
  产物 Hash/长度匹配。所有 Fixture 均非正式、dataset_eligible=false，无正式数据发布。
- 第一次全仓：1101 passed、1 failed，160.15 秒。唯一失败是旧测试
  `test_features_are_frozen_before_any_continue_or_repeat_and_bind_hashes` 的 UTC 排序断言。
  原始 Provider 记录顺序出现 `16:27:39.707150 → 16:27:39.081607`（约 0.626 秒倒退）；
  特征时间 `16:27:39.666609` 因而大于后续请求所记录的 UTC 时间。
- Provider 时间函数直接读取 datetime.now(UTC)；本地时钟实现为 CLOCK_REALTIME，预算使用
  CLOCK_MONOTONIC。底层同步/回退来源未经审计，UNKNOWN；本轮未调整系统时间。
- 完整保留失败日志和时间倒退证据，未放宽原断言、修改历史时间或改代码。隔离该测试
  定向复核 1 passed（32.14 秒），再仅进行一次带 realtime/monotonic 监测的全仓复核。
- 最终全仓 **1102 passed，0 failed，0 skipped**，154.49 秒；未改代码或放宽原断言。
  监控采样 15135 次，确认 5 次 CLOCK_REALTIME 回退，单次相邻采样 UTC 差值约
  -0.773/-0.726/-0.719/-0.909/-0.639 秒，对应 CLOCK_MONOTONIC 均前进约 10 毫秒。
  回归通过不表示时钟问题消失；原失败不是靠补写时间解决，所有失败证据原样保留。
- 两次完整回归之间没有生产实现修改，仅保留一次定向复核和一次带时钟监测的完整复核；
  原始失败 Fixture 的特征、Provider 行和 journal 已逐字节另存，后续不再反复测试到通过。

## Historical Evidence and Security

启动时 HEAD 为 `c601820`，工作区干净。对本轮开始前的 **25880 个历史文件、378 个跟踪
文件**建立 SHA-256 基线并核验未变。原正式 Run 的 4325 文件 Manifest 全部匹配，可信
Manifest SHA-256 为 `8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00`。
原 journal、每任务归档和规范化记录链独立验证通过；18 特征及原 Source/Checkpoint 不修改。

本轮新增实验室 SSH/Tunnel、HTTP/Qwen、GPU、vLLM、Docker 操作均为 0。
只运行 Fake SDK，本地资格检查不会创建真实模型客户端或检查远端服务健康。
Git 只交付实现、测试及脱敏文档；原始审计、模型消息、证书、密钥和权重不提交。

本地审计目录：`runs/phase2_budget_audit_repair/budget_audit_repair_20261128_3b529b72/`。
包含基线、专项/全仓日志、失败时间证据、新版本完整离线 Run、资格收据和
`qualification/continuation_plan.json`。计划 SHA-256：
`89671a8d3d3862a350fcb8a92e9f4f7554d76b082dd14c7d4da20ea8710bb84f`。

## Exact Continuation Review

新离线入口仅生成待审核计划，`execution_authorized=false`，没有真实运行模式。
排除全部 79 个已尝试任务，包括 `p2_api_process_invalid_001` 的 null；不重跑旧 Checkpoint。
剩余任务继承原计划的所有参数、顺序及 Worker；“是”为既定同 Checkpoint 重复目标。

| 顺序 | Task ID | Worker | 重复 |
| --- | --- | --- | --- |
| 1 | p2_api_process_invalid_002 | worker-1 | 否 |
| 2 | p2_api_repeated_state_read_001 | worker-0 | 否 |
| 3 | p2_api_repeated_state_read_002 | worker-1 | 否 |
| 4 | p2_api_not_committed_001 | worker-0 | 否 |
| 5 | p2_api_not_committed_002 | worker-1 | 是 |
| 6 | p2_logic_chain_control_001 | worker-0 | 否 |
| 7 | p2_logic_chain_control_002 | worker-1 | 否 |
| 8 | p2_logic_chain_timeout_001 | worker-0 | 是 |
| 9 | p2_logic_chain_timeout_002 | worker-1 | 否 |
| 10 | p2_logic_chain_unavailable_001 | worker-0 | 是 |
| 11 | p2_logic_chain_unavailable_002 | worker-1 | 是 |
| 12 | p2_logic_chain_invalid_001 | worker-0 | 否 |
| 13 | p2_logic_chain_invalid_002 | worker-1 | 是 |
| 14 | p2_logic_chain_stale_001 | worker-0 | 否 |
| 15 | p2_logic_chain_stale_002 | worker-1 | 否 |
| 16 | p2_logic_repeated_calculation_001 | worker-0 | 否 |
| 17 | p2_logic_repeated_calculation_002 | worker-1 | 是 |

后续真实入口需显式绑定只读 parent Run 与独立 child Run、新代码版本和本计划 Hash，
沿用已完成 79/13 而非重置整批或偷偷跳过旧停批。执行前重新核验 Worker/模型/归属/TLS，
仍为零自动重试、零 Failover。完成后审计 79+17 次唯一 Source attempts、13+6 次重复及
所有故障覆盖，再决定是否发布 Train 资格索引；原 null 永远保留，最多可能得到 95 个
有效主标签。不能为凑到 96 个有效标签补任务或重跑。Validation/Test、训练仍未授权执行。

另有真实观测到的环境遗留问题：系统实时钟持续回退，会使单纯 UTC 排序的 cutoff 检查
间歇失败。预算使用单调时钟，不受本次观测的 UTC 倒退影响；但不能因此忽略溯源门禁。
真实续跑入口验收前，应明确稳定的时间来源/可验证因果顺序。修正系统时间涉及系统级
操作，按 AGENTS.md §13 需另行授权；更改时序证据实现则须独立版本和回归。本轮不调整
系统时钟、不改既有特征/测试的时间断言，也不把真实续跑状态改为就绪。

## Git and Remaining Work

计划只提交新增实现/测试/文档，全部旧跟踪文件与历史产物不变。
最终离线验收通过后，完成敏感扫描，按仓库规范 Commit/Push，实际 Commit 见 Git 历史。
本轮没有执行上述 17+6 真实续跑；其生产入口/版本边界验收及执行仍是后续工作。

## Status: PASS

仅表示 **预算审计离线修复 Gate 通过**；不是正式采集或真实续跑 PASS。
原正式数据 Gate 仍为 BLOCKED，续跑入口和时序来源遗留问题尚未验收，不以离线
Fixture、新版本标签或最终单元测试通过改写这些状态。
