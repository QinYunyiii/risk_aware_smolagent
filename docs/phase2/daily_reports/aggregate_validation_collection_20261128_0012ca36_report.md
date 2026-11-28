# Aggregate-family Validation Collection — 2026-11-28

## Result

**新版真实 Validation 32 个任务和 7 个预选重复全部完成，整批质量与独立核验 PASS。**

- Run：`formal_validation_aggregate_20261128_0012ca36`。
- Gate：`VALIDATION_COLLECTION_COMPLETE`，quality_passed=true、passed=true。
- 主标签：**28 成功、4 有效失败、0 空标签**；32 个首个合格点 Checkpoint。
- 7 个同 Checkpoint 重复工程完整，与主标签全部一致，不作为独立样本扩样。
- 原故障覆盖条件通过，F6 committed/unknown 均实际观测。
- 连接资源释放后已发布独立 `validation_eligibility_release.json`，引用本批 32 个原始风险
  记录 Hash，明确 allowed_for_training=false。

旧 Validation `formal_validation_20261128_9e97715e` 的 32+7、26/6 标签、BLOCKED、
旧 claim 和未发布资格状态全部保持原样，不与本批拼接或择优替换。
Train 95 行资格不改写；Test、模型训练和 RETRY/REPLAN 未执行，formal_dataset_ready=false。

## Authorization and Frozen Implementation

用户在 Stage A 交付后授权“好的，执行下一阶段”。范围是完整独立新版 Validation 32+7，
复用现有两个 Worker，预算/任务/排程不变，零自动重试，无新服务或 GPU 分配。
执行 Commit **`9dda323`**，启动时工作区干净，生产代码本轮无修改。

新 experiment_id：`phase2_formal_continue_aggregate_family_v1`。
新修订 Manifest SHA-256：
`9e79ea48bb359c8eca113e669d84bfeef5f415911291fbee172be36ea69f3161`。
策略配置 SHA-256：
`f222db2e1918bae3768453277235cd18c1c57ef0b99c99b1597fd23b7624cc69`。
resolved_plan SHA-256：
`fb2e6672759465ab2f6559807781bdcdb6bb94b99e166206445bcbfc51f5d5e5`。
父 Validation Manifest SHA-256 仍为
`91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。

仅批准的 Data Analysis F2/F5 显式采用 aggregate-family-v1，共享
aggregate_data/aggregate_records 的首次匹配计数；其他故障仍用旧精确匹配。
原 Task/Catalog/Split/Prompt/Tool Schema/Verifier/捕获规则/18 特征不变。
Completion 1024、Context 8192、Seed 20261118、Temperature 0、thinking=false；
10 模型调用、12 工具调用、50000 Token、900 秒及重复继承 Source 剩余预算规则不变。

## Preflight and Remote Work

本地原 API Key/CA 环境存在；未输出或复制凭据明文。CA 可读、有效、Hash 与可信基准
一致，证书及 hostname 严格校验。专用 SSH 密钥权限/指纹、known_hosts、Alias、端口、
BatchMode 和禁止密码回退核验通过。本地 8000/8001 未占用，磁盘约 **938.70 GiB**。
只读复核原 Train **95 行资格、5318 个文件**，新版完整离线 Gate/全仓收据绑定通过。

使用新 Run ID、新审计目录执行一次带阶段记录的只读 SSH 预检，不重用旧诊断证明。
退出 0，单调耗时 **78.889 秒**，远端约 **71.640 秒**；保存 52 条阶段事件。
150 秒远端 alarm 未触发，本地监督未超时。容器 `36f825bcc5da`、root、Ubuntu 22.04、
CA、现有 Worker 进程/监听归属、七个模型文件 Hash、软件版本、间隔 GPU 采样和最终
所有权复核均通过。本次实际 Worker 0 → GPU 0、Worker 1 → GPU 1，不作为未来固定映射。
未启动/重启 vLLM、修改 GPU、远端目录/文件或进程。

仅创建本次拥有的两个 LocalForward，使用既有 `-F /dev/null` 隔离配置和显式转发。
只读 SSH 使用 ClearAllForwardings，与隧道参数区分。双 Endpoint 严格 TLS、鉴权、
`/v1/models` 的 Qwen/Qwen3-8B 身份检查通过，SDK 不连接 OpenAI 云端服务。

实际正式入口为 `scripts/run_aggregate_validation.py --mode validation
--authorize-aggregate-validation`，完整 argv、离线收据、新鲜远端证据和 Train 证明绑定
保存在本地。新策略 cohort claim 已独立创建并保留，旧 claim 不动。
每任务实际 resume(stream=True) 到终止再执行原 Verifier，没有以单次请求替代 CONTINUE。

采集监督进程退出 **0**，单调耗时 **234.610 秒**，非超时，已回收。
原始 UTC 为 2026-11-28T08:10:11.155774+00:00 至
2026-11-28T08:13:51.384297+00:00（北京时间约 16:10–16:13）。
UTC 差值与单调耗时不同；保留原时间，预算采用单调时钟，不修改系统时钟。

## Real Group Trigger Evidence

| Task | 故障 | 实际 Source + 主分支工具序列 | 实际触发 |
| --- | --- | --- | --- |
| data_aggregate_events_count_001 | F5 | read_csv → aggregate_records → aggregate_data | aggregate_records 一次 |
| data_aggregate_events_count_002 | F2 | read_csv → aggregate_records → aggregate_data | aggregate_records 一次 |

这两项 Source 在 read_csv 后捕获；恢复后首个聚合调用被拦截，随后切换到另一聚合工具
不重复注入。Checkpoint 中的 fault_identity.parameters 已保存新策略，模型和事件的
实际工具名、每工具调用序号及参数 Hash 保留。两项主结果均为成功 1。

四 Domain 的 F0/F1/F2/F3/F5 实际覆盖通过；API 额外覆盖 F4/F6，F6 committed/unknown
实际观测通过，不用排程分配冒充真实触发，也不依赖重复补主分支覆盖。

## Responses, Costs and Labels

| 指标 | 实际值 |
| --- | --- |
| 唯一 Source attempts / Checkpoints | 32 / 32，均 step 1 |
| 主有效 1 / 0 / null | 28 / 4 / 0 |
| 有效同 Checkpoint 预选重复 | 7 |
| 真实 Completion 请求 | 149：Source 32、主 CONTINUE 96、重复 21 |
| Worker 0 / Worker 1 请求 | 66 / 83 |
| Provider / Parsing 收据 | 149 / 149 |
| Parsing 状态 | success 147、not_attempted 2 |
| Provider Usage 完整且一致 | 149 / 149 |
| Finish reason | tool_calls 149 |
| Prompt / Completion / Total Tokens | 319826 / 6334 / 326160 |
| 最大观测 Completion | 222，低于固定 1024 |

两条 not_attempted 为预算前置保护的原始 Parsing 收据，不伪造 success，无额外 SDK 请求。
所有实际请求 attempt_index=1；Source、SDK 重试、任务替换、Failover 均为 0。
上表 Token 来自真实 Provider Usage，不包含 API 身份预检，也不是 Mock 成本。

四个有效 0：

- `api_order_shipped_read_001`：final_answer，tool_failure_observed。
- `api_order_shipped_read_002`：budget_exhaustion，重复也为 0。
- `p2_data_validation_control_001`：final_answer，verification_failure，重复也为 0。
- `p2_data_validation_timeout_002`：final_answer，tool_failure_observed。

这些是完整工程链路的任务失败，未将服务中断记为 0。保留实际标签和终止原因。
跨批标签差异不作为策略修订的因果收益证据，不用于挑选替代行。

## Independent Verification and Eligibility

- **39/39** 主/重复分支六类 Restore Hash、首次实际模型输入、Checkpoint 不变性、Source
  不变性、Replay Guard 通过；独立重复继承同一 Source 剩余预算及原 Worker。
- 32 份 18 特征从原 Source 重算，值、Hash 和除生成时间外的 provenance 全匹配。
  181 条因果顺序记录、SDK 请求/特征截止边界、每任务归档子集和规范化记录链通过。
- 32 个风险行逐项关联 Checkpoint 和主 outcome 的记录 Hash/标签，覆盖 16 个模板簇；
  所有记录为本次真实 Validation、新实验及新策略身份，Train/Test 请求 0。
- 新资格索引引用本批 32 个原始 risk_record Hash，绑定新 Manifest、策略、resolved_plan
  和 experiment_id。原 raw research_dataset_eligible=false 不回写，只通过派生索引发布。
  allowed_usage 为 model_selection、threshold_selection、calibration_evaluation；
  allowed_for_training=false，禁止作为 Train 权重拟合数据。
- 正式 Run Manifest 的 **1886 个文件** Hash/长度全部匹配；Manifest SHA-256：
  `942d8cfac2c94c063a6c281c2612aebf5858bbdb1f7d1142ed22afdd05751d31`。
- 资格索引 SHA-256：
  `e760af8d64cf55f1222c1ea2aa99b1db02d86d90e1407cbcfd0f4200ce05e6b3`。

**40771 个历史文件、421 个既有跟踪文件** SHA-256 未变；完整冻结链通过。
旧 Val 已用于工程设计，本批是显式修订后的完整新 cohort，不能称为从未检查过的盲测。
旧批仍 BLOCKED，无资格发布；新旧结果不合并扩样，不进入 Test 或据此声称风险模型有效。

## Resource Release and Tests

71 个本次模型客户端全部关闭。两个 SSH 隧道均退出 0、已回收，本地端口关闭。
只读核验三个 Windows TCP relay 的 PID/启动身份，原实例均不存在；未终止其他进程。
收尾不追加远端连接或模型请求，没有管理 Worker 或 GPU。

本轮离线专项 **30 passed，0 failed，0 skipped**，6.03 秒，覆盖策略匹配、身份拒绝、
Source 已消费故障后的恢复、预算边界、旧/新 claim 和收据隔离；禁止真实网络钩子生效。
compileall、diff check、原始 Manifest、风险 lineage、策略身份、资源释放和完整性核验通过。
本轮生产代码未改，不重复全仓；前置 Stage A 的 1176 passed 不计为本轮新测试结果。

实际执行/核验脚本均保存在本次预检目录：run_remote_preflight.py、run_validation_once.py、
verify_validation_final.py、verify_validation_lineage.py、verify_aggregate_policy.py、
verify_local_release.py；postrun_test_argv.json 记录实际专项命令。

## Artifacts, Git and Remaining Work

正式原始 Run：
`runs/phase2_formal_validation_aggregate_v1/formal_validation_aggregate_20261128_0012ca36/`。
独立预检/监督/核验目录：
`runs/phase2_aggregate_validation_preflight/aggregate_validation_preflight_20261128_0012ca36/`。
原始数据、请求、响应和完整审计仅留本地，Git 仅提交本脱敏报告，不上传凭据或大体积产物。

当前已获资格 Train **95 主样本**、新版 Validation **32 主样本**；各自重复只作稳定性分析。
Test 未采集，formal_dataset_ready=false。Train 仍未达到原 LightGBM 的 200 行/每类 30 门槛，
不降低门槛、不用 Val 或重复凑训练行，不自动训练。

下一步为离线数据审核和按既有预注册门槛准备可行基线方案，任何模型训练和 Test 执行
另行安排。此次阶段完成后停止，保留全部历史及新 claim，不自动采集下一 cohort。
报告按仓库规范提交/推送，实际 Commit 见 Git 历史。

## Status: PASS

限新版真实 Validation 32+7、整批质量和独立审计通过；旧 Val BLOCKED 保留，
风险模型有效性与 Test 泛化尚未验证。
