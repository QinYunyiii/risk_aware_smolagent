# CONTINUE v2.2 输出契约与工程 Gate — Stage A

日期：2026-11-27（Asia/Shanghai）。基线提交：
`b3357665930accb68a8f2b3e2981b646ddc90938`。
审查/实施 ID：`p2_continue_output_contract_stage_a_20261127_5fd31c51`。

## Objectives

用户明确批准上一轮审查报告中的新版输出契约与 Gate 判定。本轮完成本地实现、
版本绑定、专项及全仓回归，并按仓库规则交付代码和脱敏报告。
授权用于本地修订；本轮没有执行真实五任务 Gate 或正式数据采集。

## Local Environment Work

初始 main 工作区干净。读取仓库规范、最新日报/审查报告，核实旧入口、原工具
执行链、Agent State Adapter 和 Checkpoint v2 类/Prompt/输入验证逻辑后实施。
本轮只用本地 WSL 和本地任务目录/SQLite，不需要 Docker 或远端资源。

开始前对既有 10,119 个 Run 文件和 343 个 Git 跟踪文件建立 SHA-256 基线。
所有私有原始审计和测试日志存入新的独立目录：
`runs/phase2_continue_output_contract/p2_continue_output_contract_stage_a_20261127_5fd31c51/`。

## Work Completed

### 公开输出契约和无损 Observation

新增协议 risk-continue-v2.2.0、implementation_revision=output-contract-v1、
入口 frozen-continue-acceptance-v2。所有 160 个任务在 Source 开始前统一追加
公开输出契约，明确对象/数组直接返回、布尔保持布尔、精确文本保留空白，以及
calculator/unit_converter 的数字外观结果仍为字符串。

渲染器只接受原公开 prompt 和固定契约文本。TaskSpec 中除 prompt 外的字段
全部不变；没有根据 expected 或 reference_steps 构造真实 Agent 的提示。

新增版本化 Agent 子类复用原 process_tool_calls 执行链：在原 ToolOutput
进入 Memory 前，把非 final_answer 的 observation 换成无损 UTF-8 JSON 文本。
output、tool_result、工具 schema、工具本身及确定性 Verifier 均不变。
没有解析自然语言来替换最终回答，也没有将对象自动转成答案字符串。

明确拒绝 tuple、非字符串对象键、NaN/Inf、循环、非法 UTF-8 等无法无损呈现的值，
没有 str fallback。CONTINUE 中的 OutputContractError 被归为环境基础设施异常，
产生 null，不误记为 Agent 的有效失败 0。

### Source / Checkpoint / Restore 绑定

Remote Model 配置仅更改 prompt_version，推理参数、Endpoint 及预算保持原值。
Schema 保持 2.0.0；新子类在 output_contract 模块源码中定义，公开类名仍为
CheckpointableToolCallingAgent，以符合既有 Schema 的类名约束。完整模块限定名
与旧类不同，原 Agent State Adapter 继续严格验证完整类名，没有伪造类名、
修改历史快照或放宽原白名单。

Source 和 Restore 使用同一新类、Prompt Version 和新 Catalog；六类 Hash、
实际首次输入和只读 Replay 策略继续沿用。新增恢复入口还核对 Catalog/Split。
旧 Checkpoint 用新入口会在构造 Provider 前被原 Prompt/身份门禁拒绝，不能
追溯补提示或用新 Observation 编码解释旧快照。旧入口/清单仍可使用。

### 工程 Gate 与任务结果分离

- engineering_passed / passed 表示工程链路及证据有效，不表示所有任务成功。
- task_success 与 accepted_y_continue 分开：有效 1 为任务成功，有效 0 为任务失败。
- 有效 0 继续下一固定任务，不改变标签；null 或任何工程完整性门禁失败立即停止。
- 除组件 complete_chain 外，显式要求完整六项 Hash、实际首次输入、无 Replay、
  Checkpoint/Source 不变、工具 lineage、Verifier 收据、Provider Audit 及客户端释放。
  标签必须为整数 0/1，并与 Verifier 和组件标签一致。
- 最终连接释放失败仍使权威 Gate 阻塞。报告 successes、valid_failures、
  censored_or_invalid、not_attempted 和仅以有效结果为分母的任务成功率。

五个任务及 Worker 顺序、一次 Source Attempt、零重试/Failover/替换均不变。
不要求五条都成功，也不要求强行得到特定正负比例。所有本轮产物均非正式数据，
formal_collection_ready 和 research_dataset_eligible 保持 false。

## Freeze / 历史兼容性

新增独立统一清单：docs/phase2/protocol_freeze_manifest_v2_2.json。
它固定 11 个直接输入，并通过不变的 v2.1 父清单及 v1 入口清单校验全部旧依赖。
旧 99 文件父清单、v1 的 6 文件入口清单、原配置、原源码及历史 Run 原样保留。
README 仅新增新入口的说明。

| 项目 | SHA-256 |
| --- | --- |
| v2.2 统一清单 | 48ddf9fb43fbed19ac5f40bd6d14c6879a267464616b42673b9e34b36679dc11 |
| v2.2 有效 Catalog | 66ac8a5d56aaed2f7aafeac57dab918cc46d4cc3d3c8287ec60686594f16682a |
| 不变的 Split | baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9 |
| 不变的 v2.1 父清单 | c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7 |
| 不变的 v1 入口清单 | bd86d505193ee511d4c362273f62a62a8a913769e7196ba7476ab9493c7585d7 |

原真实 Run p2_continue_real_20261127_d92feb25 仍为
CONTINUE_ACCEPTANCE_BLOCKED、accepted_y_continue=0；没有事后重新标注。

## Files Changed

- src/risk_agent/collection/output_contract.py：JSON Observation 和新版本 Agent/工厂。
- src/risk_agent/collection/protocol_freeze_v2_2.py：父链、任务提示及新冻结配置校验。
- src/risk_agent/collection/continue_acceptance_v2.py：新初始化、恢复身份和工程 Gate。
- scripts/run_frozen_continue_acceptance_v2.py：独立本地/离线/需授权真实入口。
- configs 下新增独立 v2.2 profile、Remote Model、入口配置和公开契约文本。
- docs/phase2/continue_acceptance_entry_v2.md、新清单、本日报及 README。
- tests/test_continue_output_contract_v2.py：版本兼容、输出保真和 Gate 专项回归。

## Commands and Tests

测试使用新审计目录内的 offline_test_guard.py，禁止真实 socket connect/DNS/sendto/
bind 及 ssh/openssl/curl/wget 进程；Fake HTTP、Fake Process 允许。
urllib3 导入时的一次本地 IPv6 支持检测 bind 被阻断，没有外部连接。

```bash
.venv/bin/python runs/phase2_continue_output_contract/p2_continue_output_contract_stage_a_20261127_5fd31c51/offline_test_guard.py tests -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

覆盖：

- 80 模板/160 实例的参考执行、reset、原生结果 JSON 往返、除 prompt 外全字段不变。
- 五任务完整 Fake SDK 闭环：全成功、首条有效 0、五条均为有效 0。
- 实际工具→Memory→Snapshot→独立 Restore→实际模型输入的 Unicode、NUL、
  精确空白/换行、数字外观字符串、布尔、null、对象/数组保真。
- 多工具并发返回时的排序、分界和恢复后输入一致。
- T3 的更新后新 ID 重读，既有 Replay Guard 和继承预算。
- 生产 Agent Factory 经过 Mock HTTP 完整执行 T1；后续参数和 final_answer 由
  真实可见 Observation 构造，不使用 expected 填最终答案；这仍是离线测试。
- 19 类缺失/不一致证据反例、4 类完整 Runner 组件损坏反例，以及基础设施中断、
  提前终止、Context 限额、编码异常、审计失败、客户端及最终连接清理失败。
- 旧入口原回归期望不变，新旧快照交叉拒绝、真实模式授权/证据缺失时拒绝、
  新输入漂移拒绝、CLI 独占目录及产物清单精确匹配。

## Test Results

**最终全仓：1061 passed，0 failed，0 skipped，111.00 秒。**
新增 218 项，其中 160 项为逐实例契约/参考执行验证，其余 58 项覆盖生产工厂的
Mock HTTP、完整流式恢复、输出保真、Gate 反例、版本隔离及 CLI 生命周期。
原有 843 项全部通过，未改变旧 Fixture 期望。

| 最终检查 | 结果 |
| --- | --- |
| v2.2 本地 CLI 预检 | CONTINUE_ENTRY_V2_PREFLIGHT_PASS，17 文件清单校验通过 |
| v2.2 独立五任务 Mock CLI | CONTINUE_ENTRY_V2_OFFLINE_PASS，256 文件清单校验通过 |
| 本次最终独立 Stage A 审计清单 | 901 文件长度及 SHA-256 全部匹配 |
| Mock Source 捕获点 | 五个任务均为第 1 个合格 Observation |
| Mock 真实请求数 | 0；20 个 Mock Provider 响应/Parsing 记录完整 |
| 所有参考任务 / reset | 160 / 160 通过 |
| 历史 Run 文件 | 10,119 / 10,119 SHA-256 不变 |
| 旧 Git 跟踪文件 | 仅 README 按计划修改；其余 342 个 SHA-256 不变 |
| 旧父清单/v1 入口及新清单 | 均校验通过 |
| compileall / git diff --check | 通过 |

最终独立 CLI Run ID：p2_v22_preflight_final_20261127_5fd31c51、
p2_v22_offline_final_20261127_5fd31c51，位于本次私有审计根目录下。
原始命令输出分别保存 preflight_release.log、smoke_release.log；最终全仓日志为
tests_full_final.log，结构化结果为 test_results_05.json，历史/CLI 完整性结果为
release_verification.json，最终审计清单为 artifact_manifest_final.json。
此前候选版本的验证及其 619 文件清单亦原样保留。CLI 运行时实现尚未提交，实际基线 HEAD 和
git_worktree_dirty=true 均如实记录；同时保存实际入口输入和冻结 Hash。

开发阶段先后保留两个阻塞的 Mock Run：首个发现旧 Recorder 的步号回调不能被
二次绑定，修复为复用 Provider 客户端并创建独立 Recorder；第二个发现 Schema
要求的类 basename，使用版本化模块中的真正同名子类解决，完整类身份仍独立。
第三个开发 Mock 已完成五任务。后续专项曾有一个跨版本测试预期失败：旧快照
被原 Task Prompt 完整性门禁比预期更早拒绝；修正测试接受这个更早的拒绝，未
放宽生产门禁。最终静态核对还将新版 profile 的发布检查命令指向新入口，并将
旧检查保留为 parent_local_integrity_check；随后重新冻结并重新跑全仓和独立 CLI。
所有开发记录和失败日志保留，没有覆盖历史或冒充最终通过结果。

## Remote Environment Work / Resource Use

本轮真实 SSH、隧道、TLS 握手、HTTP/Completion、GPU、vLLM、Docker 操作均为 0。
本地工具及 SQLite 均在本次私有目录/pytest 临时目录执行。没有远端进程需要清理。
GitHub 交付的网络访问单列为代码交付，不是模型实验。

## Remaining Work / Status

**Status: PASS（本地 Stage A）。**
新版输出契约与工程 Gate 已实现并在独立入口启用；旧版本和历史证据保留。
尚未在真实 Qwen 上验证新输出行为，因此不能报告真实五任务 PASS 或正式采集就绪。
下一步仅在获得新的真实运行授权后，重新核验现有 Worker/连接/模型身份，使用
新入口和唯一 Run ID 执行固定五任务，仍禁止自动重试、替换、Failover 或修改预算。

## Git Commit

提交范围为本任务 13 个文件；私有 Run、审计日志、证书、凭据、模型及任务环境
均不上传。提交前敏感信息扫描及最终 Git 差异检查通过，按 AGENTS.md 第 15 节
自动 Commit / Push；实际提交 SHA 与推送结果在交付消息报告。
