# Replay Guard readonly-refresh-v1 Report

日期：2026-11-27。基线：`fd9a0aab73bc1ef96000c96e6f8f225bc2a015d4`。

## Objectives

按用户批准的最小范围修复 CONTINUE 中的新只读查询误拦截，同时保留历史调用和
不安全写入的重放保护。建立独立实现修订，不修改预算、Prompt、Split 或历史结果。

## Local Environment Work

初始工作区干净；读取仓库规范、项目指南、README、Day 14 日报、v2.1 冻结记录
及上一轮完整 CONTINUE 离线阻塞报告。实际检查 Agent 工具分发、签名 Guard、
恢复与 Verifier 调用链后实施修复。

审计目录：`runs/phase2_continue_replay_repair/p2_readonly_refresh_20261127_18007b17/`。
所有原始 Fixture、Provider Audit、Checkpoint、分支状态和测试记录只留在本地。

## Remote Environment Work

真实实验 SSH、Tunnel、TLS/API、Qwen、GPU、vLLM、Docker 操作均为 0。
使用本地 MockTransport 和临时 Fixture 凭据，没有读取用户 Key。
MockTransport 不证明真实 TLS 或 Qwen 服务通过。GitHub 交付连接单独统计。

## Work Completed

原问题是成功的 Source 只读调用被加入“工具名＋参数”黑名单；更新订单后再次
查询同一订单被当作重放，原 B3 流提前结束。本次将两个保护维度分开：

1. 新策略 `new-readonly-observations-v1` 允许新 ID 的只读 Observation。
2. 历史工具调用 ID、CONTINUE 已使用 ID、同批重复 ID 在任何工具执行前拒绝。
3. Source 已完成写入和 committed/unknown/缺失提交证据的写入仍按签名禁止重放；
   既有 not_started/not_committed 安全规则保留，幂等键不作为绕过 Guard 的理由。
4. 原默认策略 `historical-signatures-v1` 保留，旧入口不会自动改为新策略。
5. `ContinueGateRunner.from_frozen_collection(...)` 先校验修订清单，再加载其模型、
   任务、策略和协议标识，复用现有完整 resume/Verifier 路径。
6. ID 检查从恢复后的 Memory 派生，不增加 Checkpoint 字段，不改六类 Hash。
   被 ID Guard 拒绝的响应仍记录成功解析，避免 Parsing Outcome 缺失。

## Implementation Revision and Invariants

协议 ID 仍为 `risk-continue-v2.1.0`；实现修订为 `readonly-refresh-v1`；
Checkpoint Schema 仍为 `2.0.0`。

原 v2.1 配置、Manifest、Catalog、Split、模型配置及冻结记录均保留原文件。
新配置只增加实现修订、显式 Replay Policy，并引用新 Manifest。
单次 Completion 1024、Seed 20261118、Temperature 0、thinking false，
模型调用 10、工具调用 12、总 Tokens 50,000、Context 8,192、请求 120 秒、
任务 900 秒及首个合格点规则均未改变。

新 Manifest 固定 99 个本地输入。与父清单相比，仅以下四个既有实现文件 Hash
发生经批准的变化：Agent 工具分发、ContinueGateRunner、冻结加载器、离线检查 CLI。
另外固定新配置、修订说明和父 Manifest。Reference、expected、F0–F6 计划、
Verifier、风险标签和数据划分没有修改。

| 项目 | SHA-256 |
| --- | --- |
| 父 Manifest（未变） | `32e12bf71994919175e2639c6d3da04b529d56e49007486be42b6b9add22888c` |
| 修订 Manifest | `c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7` |
| 有效 Catalog（未变） | `96b9deb4d3c89cc3a1084c4fa884ca75b113daef9b29a0c14b688287b7deed2b` |
| Split（未变） | `baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9` |

默认检查当前修订；显式检查 original-v2.1 时，当前源码与旧清单不匹配会被严格
拒绝。这是预期的版本隔离，不能更新旧清单掩盖差异。复现旧实现须检出其历史 Commit。

## Offline End-to-End Evidence

Train 任务 `p2_api_process_control_001`，实际驱动 smolagents 与本地 SQLite 工具，
仅模型响应为 Mock：

`首次 get_order → 捕获 → 独立 Restore → update_order → 新 ID get_order → final_answer → Verifier`。

| 情况 | 结果 |
| --- | --- |
| 新策略、合法新 ID 查询 | 完整到达终止；Verifier=true；Mock y_continue=1 |
| 新策略、复用 Source ID | 工具执行前拒绝，不能作为新的只读查询 |
| 新策略、复用 CONTINUE ID | 工具执行前拒绝 |
| 新策略、同批重复 ID | 本批任何工具执行前拒绝 |
| 旧策略对照 | 保留原先的只读误拦截行为；没有篡改历史结果 |

成功用例恰好 4 次 Mock 模型请求：Source 1，CONTINUE 3；没有额外受控恢复请求。
实际委托 Completion 上限均为 1024。六类 Hash、首次输入、Checkpoint 不变和
Source 环境不变检查均通过。订单仅更新一次，最终 version=2，只有一个幂等记录。
累计预算保留：模型调用 1→4、工具调用 1→3、Mock Provider 报告 Tokens 50→200；
没有重新分配完整预算。所有 Fixture 客户端均已关闭。

上述正负例的原始输出保存于 `offline_e2e_evidence/`，索引为
`offline_e2e_index.json`。所有 Mock 标签均保持 formal_experiment=false、
research_dataset_eligible=false，不可用于真实模型效果统计或训练。

## Files Changed

- `src/risk_agent/agent/checkpointable_tool_agent.py`
- `src/risk_agent/collection/continue_gate.py`
- `src/risk_agent/collection/protocol_freeze.py`
- `scripts/verify_protocol_freeze.py`
- `configs/phase2_risk_collection_v2_1_readonly_refresh.yaml`
- `docs/phase2/protocol_freeze_manifest_v2_1_readonly_refresh.json`
- `docs/phase2/replay_guard_readonly_refresh_v1.md`
- `tests/test_continue_readonly_refresh.py`
- `tests/test_protocol_freeze.py`
- `README.md` 和本日报。

## Commands and Tests

本地 WSL：

```bash
.venv/bin/python runs/phase2_continue_replay_repair/p2_readonly_refresh_20261127_18007b17/offline_test_guard.py -q tests/test_continue_readonly_refresh.py tests/test_protocol_freeze.py tests/agent/test_b3_continue_gate.py tests/agent/test_checkpointable_agent.py tests/test_model_backed_checkpoint.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_continue_replay_repair/p2_readonly_refresh_20261127_18007b17/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/verify_protocol_freeze.py
git diff --check
```

另用本地完整性脚本逐项核验原始历史文件、新旧 Manifest 及本轮敏感信息扫描。
原版本检查通过函数调用验证其预期漂移拒绝，并将异常记录在 implementation_integrity.json。

## Test Results

- 新增 17 项：14 项只读/写入/完整 CONTINUE 用例，3 项冻结兼容与漂移用例。
- 最终专项：**100 passed**，11.72 秒。
- 全仓：**819 passed，0 failed，0 skipped**，73.04 秒。
- 首次专项有 4 个新增 Fixture 未补齐 ExecutionMonitor 身份字段导致构造失败；
  补齐测试元数据后专项及全仓通过，初次结果保留。
- compileall、diff 检查、敏感信息扫描、新旧清单和历史完整性检查通过。
- 7,930 个历史产物文件 SHA-256 与本轮开始前全部一致。
- 离线 Guard 禁止实际连接、DNS、发送及真实 SSH/OpenSSL/curl/wget；
  依赖的 IPv6 支持检测 bind 被拒绝，没有真实模型或远端请求。

## Acceptance Checklist

- [x] 更新后新查询可执行，且不是跳过查询后让 Verifier 通过。
- [x] 完整 resume 到 final_answer 后运行原确定性 Verifier。
- [x] 历史/重复 ID 和不安全写入仍被拒绝。
- [x] 独立 Restore、六类 Hash、精确输入和持续预算保持成立。
- [x] 新策略显式启用，旧策略行为保留。
- [x] 父冻结输入和历史 Run 原样保留，独立修订可追溯。
- [x] 全仓离线回归通过，无真实 Qwen 实验。

## Known Issues / Remaining Work

本次消除了已复现的 Replay Guard 阻塞，未声称真实 Qwen 验收或正式采集已通过。
Source 生产入口与小规模真实验收的完整接线仍需完成；不得直接以旧 Day 5 的额外
受控请求代替 Source，也不得以 Mock 标签代替真实 CONTINUE/Verifier 结果。
冻结配置的 formal_collection_ready 仍为 false。

## Status: PASS

`REPLAY_GUARD_READONLY_REFRESH_LOCAL_PASS`。
本地修复范围通过；历史 Stage A BLOCKED 和其他历史 Run 状态不变。

## Git Commit

按 AGENTS.md 第 15 节只暂存本任务文件并提交、推送 main。原始 runs、Fixture
输出、API 凭据和模型不进入 Git；提交 SHA 由最终回复和本地交付记录保存。
