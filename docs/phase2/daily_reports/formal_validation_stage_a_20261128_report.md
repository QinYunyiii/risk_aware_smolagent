# Formal Validation Entry — Stage A — 2026-11-28

## Objectives

实现 Validation 专用生产入口并完成本地离线验收，按冻结排程准备 32 个任务和 7 个
同 Checkpoint 预选重复，最低有效主样本数 24。本轮不执行真实 Validation、Test 或训练。

前置 Train 保持已验收状态：96 次唯一 Source attempts、95 个有效主样本（85 成功、
10 失败）、1 条历史空标签、19 次有效重复。旧父 Run BLOCKED、所有历史标签与产物不改写。

## Implementation

- 独立配置 `configs/formal_validation_v1.yaml` 精确引用原正式计划的 32 个 Validation Task ID
  和 7 个重复目标，不重新分配 Worker/Fault，不修改任务内容、模板 Split、Prompt 或 Verifier。
- 独立 `formal_validation_manifest_v1.json` 绑定 decision-order-v1 及全部父清单，新清单 SHA-256：
  `91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。
- 原 Source capture 和完整 CONTINUE/Verifier 方法直接继承，函数对象不替换。
  ValidationLifecycle 只对固定父记录方法机械替换 Split、新清单和 runner，AST 专项逐项比较。
- ValidationContinueRunner 保留原模型/Worker/Prompt/Verifier/Catalog/Split Hash 检查，再限定
  Validation、当前 Run、研究身份和当前执行模式；Train/Test、其他 Run 或其他模式的 CP 拒绝。
- Task Scope 和 Worker 分配在 claim/SDK 前检查，Source/分支工具事件、Provider、Checkpoint、
  规范化记录与结果都标记 Validation。Train/Test task 在任何模型工厂调用前拒绝。
- 保留原 1024 Completion、Context 8192、Seed 20261118、Temperature 0、thinking=false，
  10 模型调用/12 工具调用/50000 Tokens/900 秒及重复继承 Source 剩余预算。没有新增重试或 Failover。
- 复用预算门禁 not_attempted 收据、18 特征、同进程单调顺序/Hash 链、六类恢复 Hash、
  首次输入匹配、Source/Checkpoint 不变性和写入重放保护；不修改系统时钟或 TLS 策略。

未捕获 Checkpoint 保留为未发生，不补任务或重编码标签。Usage/审计/时序证据缺失时内层
Outcome 和外层结果均为 null，并停批；不会把本地审计故障记成 Agent 0。
单类 Validation 仍保留，AUROC/AUPRC 按原协议记 undefined，不修改标签或分布凑两类。

## Entry and Future Real Gate

`scripts/run_formal_validation.py` 默认 local-preflight；offline-gate 使用显式 Fake SDK，
两者均启用禁止真实网络/远程工具的审计钩子。离线后端不能使用默认生产工厂。
所有 Fixture 自创建时均非正式，eligibility=false；修改内存标志也不能将其发布为正式数据。

未来真实 validation 模式必须同时提供：

- 独立 `--authorize-validation`；原 Train 授权不当作 Validation 真实执行授权；
- 基于可信 Manifest 和引用索引复核的已完成 Train 证明；
- 绑定当前 Validation 清单及同一 Train 证明的完整离线 Gate 收据（local-preflight 收据不够）；
- 新鲜且绑定新 Run 的远端身份、模型/权重、GPU/服务归属证明及连接配置；
- 干净已提交代码、严格 CA/鉴权及原有连接资源所有权规则。

真实入口写入不可覆盖的 Validation cohort launch claim，换新 Run ID 也不能自动重复
同一排程。只有真实质量通过、任务不可变归档复核通过且资源已释放后才允许发布
validation_eligibility_release.json。它明确 split=validation、allowed_for_training=false，
只用于模型选择、阈值选择和校准评估，不进入 Train 权重拟合。Test 入口没有开放。

本轮只读核验 Train 的 95 行资格引用及其 5318 个父/子 Manifest 文件，未读取 Validation/
Test 历史结果，也未把 Train 样本作为 Validation 输入或据 Train 标签重新选择 Val 排程。

## Tests

- 新专项：**20 passed**，28.64 秒。
- 全仓：**1134 passed，0 failed，0 skipped**，254.27 秒。
- Source capture、CONTINUE 主方法对象一致；记录方法 AST 在明确 Scope 替换后逐项相同。
- 完整 Fake 32+7：32 个有效主分支、7 个有效重复、原故障覆盖通过；F6 实际观测
  committed/unknown。全部六类恢复 Hash、首次输入、Source/Checkpoint 不变和因果顺序通过。
- 32 份 18 特征从原始 Source 重新计算，值及 Hash 全匹配；1855 个最终离线产物 SHA/长度匹配。
- 正反身份与隔离：Train/Test 在 SDK 前拒绝；其他 Split/Run/模式的 CP 拒绝；Worker Failover
  在客户端创建前拒绝；其他 Split 或 Fixture 的 Train 证明在打开结果标签前拒绝。
- 缺失 Usage、缺失因果 cutoff 都保留 null 并停批。固定 UTC 在过去的预算边界用例仍由单调
  顺序证明有效，10 个实际模拟请求得到 10 个 Parsing 收据，无第 11 次 SDK 调用。
- 正常无 CP 的 Source 只请求一次并保留，不生成风险行或补重复；已 claim 的工作拒绝重放。
- Fixture 即使在内存中改变 offline 标志，也被不可变摘要/原始行身份检查拒绝资格发布。
- compileall、diff check、父清单链核验通过。

主要实际命令（本地 WSL `.venv`）：

```bash
.venv/bin/python runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/offline_test_guard.py tests/test_formal_validation.py -q
.venv/bin/python runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/offline_test_guard.py -q
.venv/bin/python scripts/run_formal_validation.py --mode offline-gate --run-id validation_offline_final_20261128_32d73e67 --output-root runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67 --train-proof runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/train_proof.json
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

离线审计钩子拦截的模块导入 IPv6 capability bind 不产生外部请求；所有模拟请求均为
MockTransport。测试通过不能替代尚未进行的真实 Qwen Validation 验收。

## Artifacts, Integrity and Resources

本地原始证据：`runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/`。
包含基线、生成派生方法脚本、Train 证明、专项/全仓日志、最终完整离线 Run、重算/Manifest
核验和绑定当前版本的 local_acceptance_receipt.json。离线产物不提交，也不改标记转正式。

本任务开始时 HEAD `e20e937`、工作区干净。**33177 个历史文件和 394 个已有跟踪文件**
逐项 SHA-256 未变；旧 Train 数据/资格索引、旧配置、代码及所有父 Manifest 均原样保留。
本轮仅新增 Validation 实现、配置、测试与脱敏文档。未安装依赖、操作 Docker、申请 GPU、
启动或重启 vLLM、访问 SSH、创建隧道或调用真实 HTTP/Qwen；未输出、记录或复制任何凭据明文。

## Remaining Work and Git

测试通过后按仓库规范提交、推送新增代码与本脱敏日报；实际 Commit 见 Git 历史。
真实 Validation 尚未执行，不能报告 Validation 数据已采集完成。

下一阶段的可审核范围为原 32 个一次性 Source attempts、7 个预选独立重复，最多复用
两个重新核验的现有 Qwen Worker，不启动新服务或分配新 GPU。保守 Token 总预算上限
不超过 1.95M（32×50000 + 7×最多剩余 50000），不是预计用量。实际成本逐响应记录。
执行前需独立授权、新 Run、新鲜预检、端口/磁盘和所有权核验；失败停批、不自动重试。
不包含 Test、模型训练、RETRY/REPLAN 或扩大样本数。

## Status: PASS

仅 Validation 入口实施与 Stage A 本地离线资格 PASS；真实 Validation Gate 为 NOT_EXECUTED。
