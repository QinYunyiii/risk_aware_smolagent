# Formal Validation Collection — 2026-11-28

## Result

**32 个原定任务和 7 个预选重复全部执行完成；整批质量门禁 BLOCKED。**

Run ID：`formal_validation_20261128_9e97715e`。
原始 Gate 为 `VALIDATION_COLLECTION_COMPLETE`，但 `quality_passed=false`、`passed=false`：
完成执行不等于整批取得资格。CLI 因质量门禁返回 **1**，没有崩溃或中途停批。

32 个主结果工程链完整，26 成功、6 失败，无空标签。唯一质量缺口为 Data Analysis 域
实际未触发 F2、F5。**未发布 validation_eligibility_release.json，全部风险候选仍为
research_dataset_eligible=false**，不用于正式模型选择、校准或训练。

没有追加任务、重跑、替换、改标签或放宽门禁。Train 既有资格与旧失败 Run 不变；
Test、风险模型训练及 RETRY/REPLAN 均未执行。

## Scope and Frozen Inputs

用户在带阶段记录的只读诊断交付后批准继续。执行 Commit：
`826955e`，启动时工作区干净。仅原定 Validation 32+7，复用两个现有 Worker。

Validation 清单 SHA-256：
`91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。
原正式排程、任务、Split、Prompt、Verifier、故障目标、种子、Worker 和重复目标均不变。
Completion 1024、Context 8192、Seed 20261118、Temperature 0、thinking=false；
原 10 模型调用/12 工具调用/50000 Token/900 秒及重复继承 Source 剩余预算规则不变。
SDK 自动重试、Source 重试、Worker Failover 均为 0。

## Preflight and Actual Execution

本地 CA 有效且 Hash 与可信基准一致，原 API Key 和 CA 环境已继承，未输出凭据值。
专用 SSH 密钥/known_hosts 指纹、严格身份校验和 BatchMode 通过；端口未占用，
磁盘约 938.87 GiB。只读复核 Train 的 95 行资格引用和 5318 个父/子 Manifest 文件，
完整离线 Gate 收据绑定当前 Validation 清单。

一次只读远端预检使用上一轮已验证的阶段记录程序，在新目录绑定本次真实 Run，
不复用诊断 Run 的旧证明。SSH 退出 0，单调耗时 **71.648 秒**；远端约 **65.541 秒**。
47 条阶段事件保存，150 秒 alarm 未触发；CA、容器/root/Ubuntu、现有进程/监听、
模型七个文件 Hash、软件版本、间隔 GPU 采样和最终所有权复核全部通过。
本次实际 Worker 0 → GPU 0、Worker 1 → GPU 1，未启动/重启服务或申请新 GPU。

随后只创建本次拥有的两个独立 LocalForward。双端点严格 TLS、鉴权及
`Qwen/Qwen3-8B` 模型身份预检通过。使用现有 `-F /dev/null` 隔离与显式转发方案，
只读 SSH 的 ClearAllForwardings 与隧道参数区分记录。

实际正式入口为 `scripts/run_formal_validation.py --mode validation --authorize-validation`，
完整 argv、Train 证明、离线资格和新鲜证据绑定均保存在私有预检目录。
32 个 Source 均在首个合格点 step 1 捕获，随后真正 resume 到终止并执行 Verifier。
采集监督单调耗时 **224.402 秒**，非超时，进程已回收。
原始 UTC 为 2026-11-28T06:22:00.898723+00:00 至
2026-11-28T06:25:29.749091+00:00（北京时间约 14:22–14:25）。
保留原 UTC；预算和耗时使用单调时钟，不修改系统时间。

## Counts, Costs and Outcomes

| 指标 | 实际结果 |
| --- | --- |
| 唯一 Source attempts / Checkpoints | 32 / 32 |
| 主成功 / 主失败 / 空标签 | 26 / 6 / 0 |
| 同 Checkpoint 预选重复 | 7，均工程完整，与主标签全部一致 |
| 真实 Completion 请求 | 149：Source 32、CONTINUE 96、重复 21 |
| Worker 0 / Worker 1 请求 | 67 / 82 |
| Provider Usage / Parsing 收据 | 149 / 149 |
| Parsing 结果 | success 147，not_attempted 2 |
| Finish reason | tool_calls 149 |
| Prompt / Completion / Total Tokens | 318949 / 6300 / 325249 |
| 最大观测 Completion | 222，低于固定 1024 |

所有请求 attempt_index=1、Usage 完整且与 ChatMessage 对齐。两个 not_attempted 为
原预算前置保护，不伪造 Parsing success，不增加额外 SDK 请求。API 预检不计入以上
Completion 费用。重复不作为额外独立风险样本。

六个有效 0 标签：

- `api_order_shipped_read_001`：final_answer，tool_failure_observed。
- `api_order_shipped_read_002`：budget_exhaustion；其重复也得到 0。
- `logic_convert_kilometres_metres_001`：final_answer，tool_failure_observed。
- `logic_convert_kilometres_metres_002`：final_answer，verification_failure。
- `p2_data_validation_control_001`：final_answer，verification_failure。
- `p2_data_validation_timeout_002`：final_answer，tool_failure_observed。

这些是完整链路的实测任务失败，不是通信故障补为 0。

## Fault Coverage — Direct Cause of BLOCKED

| 任务 | 冻结分配 | 冻结目标 | Source + 主分支实际工具 | 触发数 |
| --- | --- | --- | --- | --- |
| data_aggregate_events_count_001 | F5 | aggregate_data | read_csv → aggregate_records | 0 |
| data_aggregate_events_count_002 | F2 | aggregate_data | read_csv → aggregate_records | 0 |

两项主标签均为真实成功 1，没有预选重复。原 `FaultInjector._should_trigger`
（`src/risk_agent/faults/injector.py:150`）要求 target_tool 为 `*` 或与实际工具名一致。
`aggregate_data` 与 `aggregate_records` 都在公开工具集中；前者读取 CSV 后调用后者的
聚合逻辑，但二者输入接口不同。模型采用了允许的 read_csv→aggregate_records 路径，
因此精确目标 aggregate_data 从未被调用，原始 internal 事件全部 triggered=false。
这属于**指定工具未发生导致故障覆盖缺失**，不是 SSH、TLS、Usage、恢复或特征计算问题。

Data Analysis 实际覆盖 F0/F1/F3，缺 F2/F5；其他三个 Domain 的既定公共故障覆盖通过。
API 额外覆盖 F4/F6，F6 committed/unknown 均实际观测。不能依据排程“分配了 F2/F5”
就记录为实际触发，不能把这两个成功样本改为故障失败或事后改成 F0。
离线 Fixture 通过只能验证既定工具路径接线，不保证真实模型必定选择同一路径。

## Restore, Features and Integrity

- 32 个主分支、7 个重复的六类 Restore Hash、首次模型输入、Checkpoint 不变性、
  Source 不变性和 Replay Guard 均通过；同 Checkpoint 重复独立恢复、预算与 Worker 不变。
- 32 份 18 特征从原 Source 重新计算，值和 Hash 全匹配；决策顺序共 181 条记录，
  SDK/特征截止顺序、不可变归档子集及规范化记录 Hash 链通过。
- 32 个风险候选逐项关联原 Checkpoint 和主 CONTINUE outcome，标签一致；16 个模板簇，
  无 Train/Test 请求，未把 hidden fault truth 或未来分支结果加入特征。
- Run Manifest **1876 个文件** SHA-256/长度全匹配；Manifest SHA-256：
  `8ea0e39964b7c4f82d43ad4c5eec28af4989efd8d09d0fd11e5dcdc29fb37c78`。
- **36956 个历史文件、407 个既有跟踪文件** SHA-256 未变，冻结链核验通过。
- launch claim 已存在并绑定本次 Run，保留不删除；换 Run ID 不能自动重放此 cohort。

私有原始 Run：`runs/phase2_formal_validation/formal_validation_20261128_9e97715e/`。
预检、监督和独立核验：
`runs/phase2_formal_validation_preflight/validation_preflight_20261128_9e97715e/`。

## Resource Release and Tests

71 个本次模型客户端全部关闭；两个 SSH 隧道均退出 0、已回收，本地端口关闭。
按启动身份只读检查三个本次 Windows TCP relay，原实例均已不存在。
没有终止其他进程、管理远端 Worker 或在收尾追加远端请求。

本地离线 Validation 专项 **20 passed，0 failed，0 skipped**，25.80 秒，使用本次目录
中的禁止网络审计钩子。compileall、diff check、Manifest、记录链、特征重算、资格未发布
和资源释放检查通过。没有生产代码变化，不重复全仓，前置 1134 passed 不计为本轮结果。
本轮原始产物和凭据不上传，Git 仅交付本脱敏日报。

## Remaining Work and Git

当前有 95 条已获资格 Train 主样本；本批 32 条 Validation 主结果工程完整但整批未获
资格。Test 仍封存，formal_dataset_ready=false。不得使用本批做正式选择/校准，
不得据成功率宣称风险模型有效。

下一步应先离线审核“冻结工具目标”与“允许的替代调用路径”的关系，提出独立版本的
最小修订和研究影响，再由用户审核是否实施及如何处理后续 Validation。
不能按已看到的结果挑补跑任务、修改 Prompt/目标/触发语义，或事后放宽覆盖门禁。
本轮到此停止，不补跑、不改协议、不发布资格、不训练、不进入 Test。

报告按仓库交付规则提交和推送；实际 Commit 见 Git 历史。旧 BLOCKED 不改写。

## Status: BLOCKED

执行完成与工程链路核验通过；整批因 Data Analysis 的 F2/F5 实际覆盖缺失未获资格。
