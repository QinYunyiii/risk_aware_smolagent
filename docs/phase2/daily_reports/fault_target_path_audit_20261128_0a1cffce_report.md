# Fault Target and Alternative Path Audit — 2026-11-28

## Objectives and Result

**FAULT_TARGET_AUDIT_PASS**：完成离线根因审核和最小修订设计，未实施生产修订。
旧 Validation 质量状态仍为 BLOCKED，未新增正式资格、任务结果或标签。

推荐 **显式聚合操作组 + 共享首次匹配序号**，仅面向 Data Analysis F2/F5。
具体方案、边界、备选比较与后续验收见
[修订提案](../aggregate_fault_target_revision_proposal_v1.md)。
该提案状态为 PROPOSED_NOT_APPROVED_NOT_EXECUTABLE；改变故障匹配语义，实施前需要
AGENTS.md 第 4 节的研究变更确认。本轮按用户要求只审核并确定方案。

## Evidence Integrity

开始 HEAD `87ff655`，工作区干净。
实际读取上轮原始 Run 及预检目录，依其两份 Manifest 验证 **1912 个文件** Hash/长度。
其中 Validation Run 的 Manifest SHA-256 为
`8ea0e39964b7c4f82d43ad4c5eec28af4989efd8d09d0fd11e5dcdc29fb37c78`。
保护基线中的 **38871 个历史文件、408 个既有跟踪文件**，最终 SHA-256 全部不变。
活动计划、原故障实现、任务、Prompt、预算、冻结链、旧 claim 与资格均未修改。

## Call Chain and Direct Cause

1. `create_formal_tools` 暴露 Domain 的两个聚合工具；没有强制模型选某一个。
2. `LocalBenchmarkTool.forward` 用实际工具名生成签名，并将实际 ToolOperation 交给
   `FormalFaultInjector.execute`；底层 `TaskToolRegistry.execute` 在注入器的 callback 中执行。
3. `FormalFaultConfig.from_case` 原样装载 target_tool；通用 `_should_trigger` 先检查目标名称。
4. 两个案例 target_tool 都是 aggregate_data。Source 调 read_csv，主分支调 aggregate_records，
   都不匹配，原 internal 事件 triggered=false。

实际证据：

| Task | 分配 | 实际路径 | 目标是否发生 | 触发 |
| --- | --- | --- | --- | --- |
| data_aggregate_events_count_001 | F5 | read_csv → aggregate_records | 否 | 0 |
| data_aggregate_events_count_002 | F2 | read_csv → aggregate_records | 否 | 0 |

这是精确名称选择器与允许替代路径之间的设计缺口，当前注入器按冻结定义执行正确。
不能将其写成底层服务失败或将未发生的故障追记为已发生。

## Tool Relationship

`DataAnalysisTools.aggregate_data(path, ...)` 内部先 read_csv，再调 aggregate_records。
`aggregate_records(records, ...)` 可以聚合来自 read_csv 或过滤后的 records。
相同 rows 上 sum/mean/count 的结果一致，但过滤后的 rows 与完整 CSV 不等价。
因此可以为执行前 F2/F5 声明同一聚合目标组，不能把输入 Schema、参数 Hash 或实际
工具名归一成同一个工具，也不能让两种不同数据范围互相替换。

相关同类结构还有 filter_data/filter_records、search_files/search_paths；本轮不扩展修复。
仅对已执行 Train 96 和 Val 32 的 Source+主分支故障事件做 **128 项暴露审计**，
不根据标签选择方案。三组相关名称中，“原目标完全未发生、替代路径发生”的案例只有
上述两项；这不保证未来模型不会采用其他路径。

另从完整冻结排程元数据检查前瞻规则：适用 Val 2 项、Test 2 项、Train 0 项。
未检索/解析 Test 实验结果或标签。Test 配置未修改，也没有执行 Test。

## Minimal Design and State Finding

仅对显式启用 aggregate-family-v1 的 F2/F5 使用固定集合
`{aggregate_data, aggregate_records}`，组内第一个实际调用触发一次；其余默认保持旧匹配。
不把 read_csv 纳入目标，不修改工具 Schema、Prompt、Fault Taxonomy、公开错误或特征。

不能只增加“两个名字都匹配”：现有计数按工具独立，模型换名时两个调用都可能是各自
的第 1 次，从而注入两次。原型用“当前实际序号 + 其他成员计数”得到共享组序号，复用
现有 call_counts，无需新增 Checkpoint Schema 字段；保存的事件仍记录实际名称/序号。

原 `checkpoint_state` 已保存参数身份、call_counts、last_results、events、random_state。
新策略参数可随已有 fault_identity.parameters 绑定；恢复到不同策略应拒绝。
这只是状态结构可复用，不代表任意旧 adapter 都能安全运行新策略。正式实现仍须用
独立 Manifest、工厂和入口绑定，并在 SDK 前阻止不匹配版本；本轮未声称完成这一 E2E。

## Offline Feasibility and Limitations

私有原型继承原 FormalFaultInjector，只替换候选匹配谓词，未由生产代码导入。
实际保存的两段路径前缀，在候选匹配规则下均于第 2 个事件 aggregate_records 首次匹配。
这里只验证匹配谓词，停止在新错误处；没有重放原参数、调用 Agent 或推测新标签。
不能由此推断换策略后模型必定恢复、必定失败，或真实覆盖必定通过。

本地专项合计 **30 passed，0 failed，0 skipped**，4.64 秒：

- 候选原型 **18 项**：两工具/两故障/两调用顺序、切换后不重复、恢复前后计数、
  无组调用不注入、无 opt-in 保留旧行为、跨策略恢复拒绝、sum/mean/count 等价条件、
  过滤输入不等价、未知策略/不支持故障拒绝及朴素逐工具计数的二次注入反例。
- 原有故障及 F5 恢复回归 **12 项**，原测试不修改。

实际命令通过本次目录的 offline_test_guard.py 运行：

```bash
.venv/bin/python <本次审计目录>/offline_test_guard.py \
  <本次审计目录>/test_proposal_feasibility.py \
  tests/test_faults.py \
  tests/test_formal_train_collection.py::test_no_progress_has_real_no_execution_and_restored_cursor -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

禁止网络的测试钩子生效；compileall、冻结链和最终 Manifest 核验通过。
没有生产代码变化，不重复全仓测试。原型/反事实匹配产物均非正式数据，不发布资格。

## Resources, Artifacts and Git

实验室 SSH、隧道、TLS 握手、HTTP/API、Qwen、GPU/vLLM/Docker 操作均为 **0**。
只读取本地证据并执行本地纯工具/原型用例，没有远端资源需要清理。GitHub 文档交付的
Git Push 单独计，不作为离线实验/诊断请求统计。

本地私有审计目录：
`runs/phase2_fault_target_audit/fault_target_audit_20261128_0a1cffce/`。
保留基线、输入 Manifest 核验、128 项暴露投影、前瞻元数据范围、原型、反事实匹配、
30 项测试、完整性核验和最终 Manifest。原始轨迹、参数和大体积产物不上传。

仅新增本日报和修订提案，按仓库规范检查并提交/推送，实际 Commit 见 Git 历史。

## Remaining Work and Status

先审核提案，若批准，下一步仅做新版本 Stage A 实现及离线 E2E。真实新 Val cohort
另行批准；不只补两项与旧 30 项拼接，不删除旧 claim，不降低覆盖门禁，不自动进入 Test。

**Status: PASS（仅离线审计与提案）**。
生产规则未修订，旧 Validation 仍 BLOCKED；本轮未执行任何正式实验。
