# Aggregate Fault Target Revision — Stage A — 2026-11-28

## Objectives and Scope

用户批准离线提案后的 Stage A 实施与验收，并要求继续。本轮只新增显式聚合目标策略、
独立 Validation 入口、版本清单和离线测试，不执行真实采集。
起始代码 `1947128`，工作区干净。

旧活动配置、生产代码、Schema 和所有历史结果不改。旧 Validation
`formal_validation_20261128_9e97715e` 的 32+7、26/6 标签、质量 BLOCKED、旧 claim、
未发布资格状态全部保留；Train 95 行资格不改写，Test 仍封存。

## Implementation

- 新 `AggregateFaultInjector` 继承冻结 FormalFaultInjector。只有显式参数
  `target_match_policy=aggregate-family-v1` 才启用 Data Analysis F2/F5 的固定组
  `{aggregate_data, aggregate_records}`，组内第一次实际调用触发一次。
- 共享序号从已有每工具计数推导：当前实际序号加其他成员计数。F2 增计数后与 F5
  增计数前均适用；恢复后换工具不会再次注入。不增加 Checkpoint Schema 字段。
- 事件名、实际工具序号、参数 Hash、公开 Tool Schema、Replay Signature 和错误文本不变。
  未启用策略的 case 保持精确匹配；未知策略、不支持 Domain/Fault/目标/序号及写标志拒绝。
- 新合同在原完整 Validation/因果/预算/生产者/协议链校验后，深拷贝原计划，仅为适用
  case 增加策略参数，使用独立 experiment_id。原任务、Split、排程和所有配置文件不改。
- 仅元数据规则适用 Val 2、未来 Test 2、Train 0；新入口只执行原 Validation 32+7。
  resolved_plan、策略 Hash、修订 Manifest Hash、独立实验身份在请求前写入记录并传播。
- 新 Collector 直接继承原 `_capture`、`_packet`、`_branch`、`_persist_records`、summary 和
  run_validation；完整 Source/CONTINUE/Verifier 方法和原覆盖条件不复制或修改。
  新故障工厂只对 opt-in case 创建新 adapter；SDK 前核验实际 adapter 类型和配置身份。
- 策略身份随原 fault_identity.parameters 和原计数保存。跨策略恢复拒绝；旧实验/其他 Run/
  Split/模式的 Checkpoint 仍由原身份门禁拒绝。新旧形状相同不代表语义可互换。
- 新资格发布路径保留原真实身份/资源/完整性条件，再绑定新策略与记录 provenance；
  Fixture 永不发布正式资格，Validation 明确 allowed_for_training=false。

Completion 1024、Context 8192、Seed 20261118、Temperature 0、thinking=false、原模型/
工具/Token/墙钟预算、重复继承剩余预算和 18 特征不变。未新建连接/TLS/状态管理框架。

## Files and Frozen Revision

仅新增：

- `configs/aggregate_fault_target_v1.yaml`。
- `src/risk_agent/collection/aggregate_target_policy.py`、`aggregate_target_contract.py`、
  `aggregate_validation.py`、`aggregate_validation_fixture.py`。
- `scripts/run_aggregate_validation.py`。
- `docs/phase2/aggregate_fault_target_manifest_v1.json`、`aggregate_validation_entry_v1.md`。
- `tests/test_aggregate_target_policy.py`、`test_aggregate_validation.py` 和本日报。

新 Manifest SHA-256：
`9e79ea48bb359c8eca113e669d84bfeef5f415911291fbee172be36ea69f3161`。
策略配置 SHA-256：
`f222db2e1918bae3768453277235cd18c1c57ef0b99c99b1597fd23b7624cc69`。
原 Validation Manifest SHA-256 仍为
`91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。

## Entry and Cohort Safety

新入口默认 local-preflight，offline-gate 用显式 Mock SDK；两种模式禁止真实网络和
远程工具。新的 Fixture 特别执行 read_csv→aggregate_records 的真实本地工具路径。
它读取 reference/expected 仅用于生成模拟响应，不用于生产模型或正式数据。

未来真实模式另外要求 `--authorize-aggregate-validation`、可信 Train 资格证明、
绑定新 Manifest/策略及同一 Train 证明的完整离线 Gate 收据，以及新鲜远端证明、干净
已提交代码、严格 TLS、鉴权、Worker 归属、端口和资源门禁。旧 Validation 收据会被拒绝。

新 cohort claim 在独立 `phase2_validation_target_control` 下按原排程 Hash+固定策略配置
Hash 定位，内容绑定新代码 Manifest。只改 Run ID 或重建代码 Manifest 不能重放同一
策略的 cohort；旧 claim 不删除。本轮没有创建真实 claim。

未来若授权真实采集，完整运行独立新版 32+7，不补两项与旧 30 项拼接。不把旧 Val
称为未曾检查的样本，需披露其用于工程设计。Test 和训练不在本轮授权范围。

## Offline Gate and Independent Verification

CLI Run：`aggregate_validation_offline_20261128_7ace54af`。
完整 32 主分支、7 原预选重复均通过工程与原覆盖 Gate；27/5 为 **Mock 标签**，
不是新增正式数据，formal_experiment=false、research_dataset_eligible=false。

- F5 `data_aggregate_events_count_001`、F2 `_002` 均在主分支 aggregate_records 上触发
  **恰好一次**；Source 的 read_csv 不触发。组成员切换和恢复后不会再次注入。
- 32 份 18 特征及除生成时间外的原始 provenance 从 Source 独立重算，值/Hash 匹配。
- 39 个分支的六类 Restore Hash、首次输入、Source/Checkpoint 不变性和 Replay Guard
  通过；每任务因果顺序、规范化记录 Hash 链、不可变归档及风险→Checkpoint/主 outcome
  关联匹配。独立实验身份和策略字段全程可追溯。
- 最终离线 Manifest 的 **1864 个文件** Hash/长度全匹配，Manifest SHA-256：
  `9f60c659b769d7d5316f2cca480d4ce70a1af513ec460577234a33d6f6eb4d36`。
- 实际只读核验原 Train 95 行资格及其 **5318 个文件**，未把它们作为 Validation 输入。
- qualification_receipt 明确 qualification_only=true、execution_authorized=false，
  绑定新修订；没有正式资格索引、真实样本或真实 cohort claim。

## Tests

专项 **42 passed**，64.18 秒。首轮有 1 个新增测试替身缺少 checkpoint_path，修正
测试替身后通过；没有修改原身份判定逻辑来迎合测试。最终全仓另含修订后的恢复重复用例。

专项范围：

- 两成员、两顺序、F2/F5、共享首次计数、未知/缺失策略、非法范围、写操作拒绝。
- 首次组调用前捕获：分支正确触发；首次组调用后捕获：主与测试专用独立恢复分支均
  不再触发。测试专用分支不是正式排程或额外风险样本。
- 原精确匹配不变；新旧策略的 fault_identity 不匹配恢复拒绝。
- 无组调用的完整 32+7 Mock Gate 即使主结果工程完整，原故障覆盖仍失败，不发布资格。
- Source/CONTINUE Usage 缺失保留 null 并停批；预算边界仍只发生 10 次 Mock SDK，
  对应 10 条 Parsing 收据，末条 not_attempted，无第 11 次 SDK。
- 原生命周期函数对象复用；Train/Test、旧实验和错误 adapter 在 SDK 前拒绝；
  策略变动在 claim 前拒绝；旧资格收据拒绝，Fixture 不可改内存标志转正式。
- 新 cohort claim 不触碰旧 claim，改变 Run ID 或重建代码 Manifest 均不能重放。

最终全仓回归 **1176 passed，0 failed，0 skipped**，323.13 秒；包含全部 42 项新专项
及 Source 已消费故障后的测试专用重复恢复用例。compileall、历史完整性和 diff check 通过。
本地资格收据绑定当前修订/策略/Train 证明/完整离线 Gate 和全仓结果，
execution_authorized=false，real_validation_executed=false。

主要实际命令：

```bash
.venv/bin/python <本次审计目录>/offline_test_guard.py tests/test_aggregate_target_policy.py tests/test_aggregate_validation.py -q
.venv/bin/python <本次审计目录>/offline_test_guard.py -q
.venv/bin/python scripts/run_aggregate_validation.py --mode offline-gate --run-id aggregate_validation_offline_20261128_7ace54af --output-root runs/phase2_aggregate_target_stage_a/aggregate_target_stage_a_20261128_7ace54af --train-proof runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/train_proof.json
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Integrity and Resources

本地审计目录：`runs/phase2_aggregate_target_stage_a/aggregate_target_stage_a_20261128_7ace54af/`。
包含基线、生成/构建记录、专项和全仓日志、完整 CLI 离线 Run、独立重算/Manifest 核验、
新收据、历史完整性记录和最终审计 Manifest。原始产物不提交 GitHub。

**38887 个历史文件、410 个已有跟踪文件**逐项 SHA-256 未变，所有父冻结链通过。
本轮没有实验室 SSH、隧道、TLS 握手、HTTP/Qwen 请求、GPU/vLLM/Docker 操作、Test 采集
或模型训练。GitHub 交付仅进行仓库 Git Push，不计为实验网络请求。

## Remaining Work and Status

审核本次 Stage A 后，才可另行授权完整新修订 Validation 32+7。仍须重新做远端预检，
使用新 Run 和新鲜证据，失败停批、零自动重试，不降低覆盖门槛。
真实模型可以不调用组内工具，离线 Gate 不保证真实覆盖通过。

提交/推送按仓库规范交付，实际 Commit 见 Git 历史。旧 Validation BLOCKED 不改写，
正式数据集仍未就绪。本轮完成后停止，不自动进入真实实验。

## Status: PASS

`AGGREGATE_TARGET_STAGE_A_PASS`，仅限新实现和本地离线资格；新版真实 Validation
尚未执行，旧批质量 BLOCKED 保留。
