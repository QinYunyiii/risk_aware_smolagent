# Frozen CONTINUE Entry — Stage A Report

日期：2026-11-27。基线 Commit：`b0e347c8a67b3ad1373a3e674cc1586843557cc6`。

## Objectives

完成同一入口下的冻结配置加载、Source 首个合格决策点捕获、独立 Restore、
六类 Hash、首次输入匹配、完整 CONTINUE 到终止、确定性 Verifier 和资源审计。
本轮仅执行本地接线和离线验收，不运行真实 Qwen 或正式采集。

## Local Environment Work

初始工作区干净。已读取 AGENTS.md、PROJECT_GUIDE.md、README、Day 14 日报、
最新 readonly-refresh 修复日报及当前冻结配置/实现。复用现有 Agent Factory、
RecordingModel、DecisionCheckpointCapture、CheckpointManager、ContinueGateRunner、
ProviderResponseAudit 和 ConnectionLifecycle。

独立审计目录：
`runs/phase2_continue_entry/p2_continue_entry_stage_a_20261127_7cc97cde/`。

## Remote Environment Work

真实实验 SSH、Tunnel、TLS/API、Qwen、GPU、vLLM、Docker 操作全部为 0。
未读取用户 API Key 或 CA；离线 SDK 使用专用 MockTransport 和 Fixture 凭据。
GitHub 交付连接单独统计，不属于实验操作。

## Work Completed

- 新入口 `scripts/run_frozen_continue_acceptance.py` 提供 local-preflight、offline-smoke
  和需要单独授权的 real-five-task-gate 三种模式；默认仅本地预检。
- 固定五个 Train 任务及 Worker 0/1/0/1/0 顺序，每任务一次 Source Attempt；
  零自动重试、零任务替换、零 Worker Failover，任一失败立即停止。
- 新 Source 生产者对每个完成的 ActionStep 检查捕获；首个合格 Observation/Error
  后立即停止 Source，不调用旧 Day 5 的额外受控恢复请求。
- 原 ContinueGateRunner 使用 readonly-refresh 策略，独立 Restore 后真正消耗
  resume(stream=True) 到终止，再运行原确定性 Verifier。
- Source/CONTINUE 使用同一冻结参数及同一个任务起点；薄 RecordingModel 适配
  将请求限制到剩余 wall 时间，并在工具执行前检查实际 Provider Context 用量。
  Source 预算随 Checkpoint 恢复，不重新分配完整预算。
- Provider Response、SDK 可见原始快照、Usage、Finish Reason、Latency、Parsing
  Outcome 和请求 ID 关联统一保存。Parsing 记录按 request ID 关联，不假定其含 task_id。
- 独立检查 Source 环境、Runtime 和模型计数不变；任何恢复、审计或资源检查失败，
  不接纳为有效 0/1，入口 accepted_y_continue 保持 null，组件原始输出不覆盖。
- 手动中断先关闭本次客户端，再释放中断请求标记，保持连接管理器能够清理拥有的
  隧道；不伪造 Provider 响应。SDK 客户端的关闭状态按其实际方法/属性接口核验。
- 真实模式沿用现有 Worker/权重/版本/GPU 证据校验；额外绑定唯一 Run ID，要求
  checked_at、Worker 所有权时间和 GPU 采样均新鲜，并核对容器、用户及 Listener。
  凭据和 CA 路径在获准真实模式、创建隧道前检查，值不写入产物。

## Frozen Inputs and Entry Pinning

原 99 个冻结文件、原配置和所有历史 Run 不变。研究协议仍为 risk-continue-v2.1.0，
实现仍为 readonly-refresh-v1，Checkpoint Schema 仍为 2.0.0。
预算、Seed、Temperature、Prompt、Tool Schema、Catalog、Split、故障计划、Verifier
和风险标签均未修改。

新增入口清单固定 6 个输入：两份新增实现、CLI、范围配置、入口说明及复用的既有
API/远端核验辅助脚本。每个 Run 保存入口清单和其输入的精确副本，避免只记录 HEAD
而无法追溯本轮尚未提交时执行的新接线代码。
清单还记录工作区是否有未提交修改；Source task_run_id 绑定本 Run ID，避免跨 Run 同名。

| 项目 | SHA-256 |
| --- | --- |
| 父冻结清单（未变） | `c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7` |
| 入口清单 | `bd86d505193ee511d4c362273f62a62a8a913769e7196ba7476ab9493c7585d7` |
| 有效 Catalog（未变） | `96b9deb4d3c89cc3a1084c4fa884ca75b113daef9b29a0c14b688287b7deed2b` |
| Split（未变） | `baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9` |

## Artifacts Generated

最终本地预检 Run：`p2_continue_preflight_20261127_7cc97cde_final`。
Gate：CONTINUE_ENTRY_PREFLIGHT_PASS；12 个 Artifact Hash 全部匹配。

最终离线 Run：`p2_continue_offline_20261127_7cc97cde_final`。
Gate：CONTINUE_ENTRY_OFFLINE_PASS；251 个 Artifact Hash 全部匹配。
权威结果文件均为 gate_summary_final_audit.json，晚于资源释放审计发布。

| 任务 | Worker | 捕获 Step | Source Mock 请求 | CONTINUE Mock 请求 | Verifier |
| --- | --- | --- | --- | --- | --- |
| p2_data_region_units_sum_001 | 0 | 1 | 1 | 3 | true |
| p2_file_report_control_001 | 1 | 1 | 1 | 3 | true |
| p2_api_process_control_001 | 0 | 1 | 1 | 3 | true |
| p2_logic_chain_control_001 | 1 | 1 | 1 | 3 | true |
| p2_data_region_units_sum_002 | 0 | 1 | 1 | 3 | true |

共 20 次 Mock 请求：Source 5、CONTINUE 15。五个六类 Hash 检查、五个首次输入
匹配、五个 Source 不变检查和客户端释放检查均通过；无额外受控恢复请求。
五个 Mock 标签均为 1，formal_experiment=false、research_dataset_eligible=false。
这些值只证明工程链路，不属于真实 Qwen 效果或正式训练数据。

保留 baseline、分次测试汇总、完整预检/离线 stdout/stderr、输入快照、Provider
审计、完整 Checkpoint、分支模型/工具/Agent 轨迹、Verifier、预算和资源记录。
stage_a_acceptance_final.json 保存最终完整性核验摘要；原始材料不上传 Git。

首个开发试跑 offline_wiring_001 因汇总器误用 Parsing.task_id 而 BLOCKED，底层
组件已完成恢复/Verifier。修正请求 ID 关联后使用新的 Run 试验，原失败结果保留。
最终验收使用上表的新 Run ID，没有把开发失败重标为 PASS。

## Files Changed

- `src/risk_agent/collection/continue_acceptance.py`
- `src/risk_agent/collection/continue_acceptance_fixture.py`
- `scripts/run_frozen_continue_acceptance.py`
- `configs/continue_acceptance_v2_1.yaml`
- `docs/phase2/continue_acceptance_entry_manifest_v1.json`
- `docs/phase2/continue_acceptance_entry_v1.md`
- `tests/test_continue_acceptance_entry.py`
- `README.md` 和本日报。

## Commands and Tests

本地 WSL：

```bash
.venv/bin/python runs/phase2_continue_entry/p2_continue_entry_stage_a_20261127_7cc97cde/offline_test_guard.py -q tests/test_continue_acceptance_entry.py tests/test_continue_readonly_refresh.py tests/test_protocol_freeze.py tests/agent/test_b3_continue_gate.py tests/agent/test_continue_outcome.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_continue_entry/p2_continue_entry_stage_a_20261127_7cc97cde/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode local-preflight --output-root runs/phase2_continue_entry/p2_continue_entry_stage_a_20261127_7cc97cde --run-id p2_continue_preflight_20261127_7cc97cde_final
.venv/bin/python scripts/run_frozen_continue_acceptance.py --mode offline-smoke --output-root runs/phase2_continue_entry/p2_continue_entry_stage_a_20261127_7cc97cde --run-id p2_continue_offline_20261127_7cc97cde_final
git diff --check
```

另做父冻结清单、入口清单、所有发布 Artifact、历史文件 Hash 及敏感信息扫描。

## Test Results

- 新增专项 24 项：五任务全链路、首个合格点、可见工具错误、终止无捕获、
  服务中断、错误最终答案、Context 超限、共享 wall 上限、审计失败、两阶段手动
  中断清理、重复尝试/错 Worker、入口漂移、未授权真实模式、无真实凭据的 CLI、
  精确 Manifest、Run 不覆盖及新鲜度/身份/Run 绑定检查。
- 最终相关专项：**69 passed**，19.61 秒。
- 全仓：**843 passed，0 failed，0 skipped**，82.00 秒。
- compileall、diff 检查、敏感信息扫描通过。
- 父 99 个文件全部匹配；8,175 个历史产物文件 SHA-256 与本轮开始前一致。
- 测试 Guard 阻止真实网络及远端工具；依赖的 IPv6 支持探测 bind 被拒绝。

## Acceptance Checklist

- [x] 同一入口完成 Source、首个合格点、独立 Restore、完整 CONTINUE 和 Verifier。
- [x] 五任务固定顺序，零重试、零替换、零 Failover、失败即停。
- [x] 六类 Hash、精确输入、Readonly Refresh Guard、持续预算和状态隔离通过。
- [x] Provider/Parsing 审计完整；审计和基础设施失败不会被接纳为有效失败标签。
- [x] 手动中断和正常路径资源释放有离线验证。
- [x] 真实模式缺少授权/新证据时不会创建实验目录或连接。
- [x] 入口输入和 Run Artifact 均可按 Hash 复核，历史产物不变。
- [x] 全仓回归通过；未执行真实实验、正式采集或 RETRY/REPLAN 扩展。

## Known Issues / Remaining Work

本地接线及离线验收已完成，不再缺少 Source/CONTINUE 统一入口。
尚未核验当前真实 SSH、Worker、GPU 映射、CA/TLS、模型身份或真实 Qwen 行为；
这些只能在用户单独授权后重新检查。离线 SDK 不证明远端服务当前可用。

下一阶段为同一已固定入口的五任务真实验收：使用新 Run ID、当前所有权/权重/版本
证据、既有两个 Worker、严格 TLS、一次 Source Attempt 和失败即停规则。
不启动/重启 vLLM，不抢占 GPU。真实验收通过并审核后再决定正式采集。

## Status: PASS

**FROZEN_CONTINUE_ENTRY_STAGE_A_PASS**。
本地统一入口及离线验收通过；真实 Gate 未运行，formal_collection_ready=false。

## Git Commit

按 AGENTS.md 第 15 节只提交本次代码、配置、测试和脱敏文档并推送 main。
原始 runs、私有证据/连接配置、Key、CA 和模型均不上传；最终 SHA 保存在最终回复
和本地 Git 交付记录中。
