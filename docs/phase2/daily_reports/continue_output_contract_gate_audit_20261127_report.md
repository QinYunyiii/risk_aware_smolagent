# CONTINUE 输出契约与工程 Gate 离线审查

日期：2026-11-27（Asia/Shanghai）。执行基线：
`70f6115d9f8de707d795e14a0d13a8c7598c99e4`。
审查 ID：`p2_continue_contract_audit_20261127_d0835de0`。

## Objectives / 执行摘要

核实最近真实 T1 的格式失败，以及工程 Gate 是否应接纳证据完整的有效 0 标签。
本轮完成离线审查、全部任务的本地参考执行、修订草案和草案的反例检查。
**审查结论成立；修订尚未启用，真实五任务 Gate 仍然 BLOCKED。**

1. T1 已算出正确字典，但 final_answer.answer 返回自然语言字符串；原 Verifier
   的 false 和原标签 0 符合现有实现。公开任务提示没有明确要求保持结构化类型。
2. 现有入口把“有效结果是否采集完整”和“任务是否成功”合并为 passed，导致
   一个证据完整的有效 0 也会停止五任务工程验收。这是当前实现和测试明确规定的
   行为，不能声称旧入口违反了它自己的配置，也不能事后改写旧 Run。
3. 全任务审查发现精确文本的可观测性限制：框架把普通工具返回值格式化为
   str(tool_call_result).strip()。26 个 read_file 任务的参考结果包含边界空白，
   当前格式化会丢失这些信息。仅补提示不足以保证所有任务的输出契约可被满足。

推荐一次性处理“公开输出格式及无损文本呈现”和“工程 Gate 与任务成功率分离”，
完成离线回归并发布新版本后，再单独申请真实五任务验收；无需回到 SSH/TLS 排查。

## Local Environment Work / 历史证据完整性

初始分支 main，工作区干净。已读取 AGENTS.md、PROJECT_GUIDE.md、README.md、
最近 Day 14 日报，以及最新冻结入口、Stage A 和真实 Stage B 报告/实现。
本轮只需本地 WSL，不需要 Docker、SSH、HTTPS API 或 GPU。

历史真实 Run：
`runs/phase2_continue_acceptance/p2_continue_real_20261127_d92feb25/`。
读取原 Provider Response、真实工具记录、最终 Gate、资源释放和原 Artifact Manifest，
没有以日报摘要替代原始证据。原清单内 71 个文件的长度及 SHA-256 全部匹配。
本轮开始对 runs 中既有 9,145 个文件及 342 个 Git 跟踪文件建立 Hash 基线。

| 当前冻结输入 | SHA-256 |
| --- | --- |
| readonly-refresh 父清单 | c9bc13ba1e846e0d0f2050b044ef0e525603bc071cbcab769e5e6668fe9253f7 |
| 统一入口清单 v1 | bd86d505193ee511d4c362273f62a62a8a913769e7196ba7476ab9493c7585d7 |

两层清单及其固定文件校验通过。原 Source、Checkpoint、分支、标签、失败分类、
历史 Manifest、Catalog、Split、预算及所有生产实现均未修改。

## Work Completed / T1 实际调用链

原 Source 在第一次 read_csv Observation 后捕获；独立 Restore 后实际执行
filter_records、aggregate_records、final_answer。六类 Hash、首次模型输入、
Replay Guard、Source 不变、Checkpoint 不变、Provider/Parsing 审计均通过。

aggregate_records 的实际返回值等于原 expected.result；原 final_answer.answer
是自然语言字符串。这两个值从原始工具记录及原始 Provider Tool Call 分别读取。
`src/risk_agent/benchmark/verifier.py:28` 直接执行 result == expected["result"]，
并继续核验 expected 中的文件/数据库状态；不会抽取自然语言中的数字。

在全新的本地任务环境中调用同一个 Verifier：原回答仍为 false，已观察到的工具
字典为 true。这是离线对照，不是新的模型回答或标签，未对原 0 重新标注。
Verifier 使用 Python 相等比较，并不是独立 JSON Schema 类型校验器；本次不增加
更严格的数值/布尔类型规则，也不放宽原比较规则。

代码位置：

- `src/risk_agent/collection/continue_acceptance.py:341`：完整链路、Source 不变和
  Provider 审计共同决定 accepted_y_continue 是否可接受。
- 同文件第 346 行：passed 还要求 verifier_passed 为 true。
- 同文件第 381 行：passed 为 false 时停止固定任务序列。
- `src/risk_agent/collection/continue_gate.py:853`：组件 complete_chain 接纳有效 0/1，
  同时检查 Verifier 收据、六类 Hash、首次输入、Replay、Checkpoint 和工具 lineage。
- `src/risk_agent/collection/continue_outcome.py`：原三态标签逻辑已正确区分有效失败
  和基础设施中断，建议保留该逻辑。

## 全 Catalog 输出契约审查

80 个模板、160 个实例全部在新建本地环境执行原 ReferenceTaskRunner。
160/160 Verifier 通过，160/160 reset_verified，最终结果类型全部与原 expected 一致。
参考执行只证明任务及工具的确定性可运行性，不证明真实 Agent 成功率。

| 最后一个参考工具 | 实际原生结果类型 | 实例数 |
| --- | --- | ---: |
| aggregate_data / aggregate_records | object | 28 |
| get_order / query_inventory / update_order | object | 40 |
| filter_data | array | 12 |
| list_files / search_files | array | 14 |
| read_file | string | 26 |
| calculator / unit_converter | string | 18 |
| validate_result | boolean | 22 |

合计：68 object、26 array、44 string、22 boolean。不能统一要求“返回 JSON 对象”，
也不能把数字外观的 calculator/unit_converter 字符串转为 JSON number。
本轮没有用 Test 结果选择模型参数、阈值、示例或成本权重；expected 仅用于离线
确定性一致性检查，未送入真实模型或新的公开提示。

当前 LocalBenchmarkTool.output_type 和 FinalAnswerTool.answer 都是 any，
仅凭这两个 Schema 无法向模型声明完整输出契约。

## 精确文本可观测性

安装的 smolagents 1.26.0 在 ToolCallingAgent.process_tool_calls 中执行
str(tool_call_result).strip()；ActionStep.to_messages 再将该 Observation 放进模型输入。
项目子类保留了这段普通工具格式化路径。

26 个 read_file 任务的预期文本均包含会被 strip 丢失的边界空白；将对应字符串
strip 后交给原 Verifier，26/26 均失败。另用实际冻结 Adapter、Mock SDK、完整
Source/Restore/CONTINUE 跑 T2，直接检查最终请求中的 read_file Observation：
原文本末尾换行已消失。

该 Mock 最终仍成功，因为离线 Fixture 显式使用 hidden expected 构造 final_answer，
这是它用于验证工程连通的设计。**不能据此声称模型可从 Observation 精确恢复原文。**
这也不证明真实 T2 必然失败；T2 本轮没有真实运行，模型仍可能猜中换行。

## 最小修订草案：公开输出契约（未启用）

建议在新的版本中为所有任务使用同一段公开说明，不只为 T1 补一句定制提示：

> Return the requested result using final_answer(answer=...). Preserve the result's
> native JSON type and contents. Pass objects and arrays directly, not as prose or
> JSON-encoded strings. Pass Boolean results as booleans. For exact text, preserve
> every character, including leading/trailing whitespace and newlines. calculator
> and unit_converter return strings; keep numeric-looking strings as strings.
> Do not add explanations, Markdown fences, or extra wrapper keys. Tool observation
> values are JSON-encoded; decode the JSON value before returning it as answer.

最后一句只可与下述无损呈现同时启用：对本地 benchmark 工具的 JSON 兼容结果，
用 JSON 编码呈现 Observation 的值，显式保留 string 的引号、转义换行和布尔类型。
不把整段 JSON 文本直接当作最终 answer，不转换原始 tool_result，不读取 expected
来构造真实 Agent 的输出。非 JSON 值必须有明确错误处理，不能静默退回有损格式。

公开格式根据公共工具契约定义，不从 hidden expected 推导动态答案、参考步骤或
字段值；不增加演示答案，不在 Source/Restore 之间临时补提示。
保留原 Verifier、文件/数据库检查及 0/1/null 语义，不做正则抽数、自然语言宽松
比较、LLM Judge、strip 后比较或自动替换 final_answer。

## 最小修订草案：工程 Gate（未启用）

建议拆开 engineering_passed、task_success 和 accepted_y_continue：

| 结果 | 工程任务验收 | 任务成功 | 固定序列行为 |
| --- | --- | --- | --- |
| 完整链路、审计/清理通过、Verifier=true、Y=1 | true | true | 继续下一固定任务 |
| 完整链路、审计/清理通过、Verifier=false、Y=0 | true | false | 继续下一固定任务 |
| Y=null、缺失证据或任一完整性门禁失败 | false | 未确定 | 立即停止 |
| 客户端清理或最终连接释放失败 | false / 最终 Gate 阻塞 | 单独保留 | 停止，不发布 PASS |

不能只把 passed 改成“有标签”：仍必须要求原 complete_chain、六项 Hash 齐全且为
true、首次输入一致、无 Replay、Checkpoint/Source 不变、工具 lineage 完整、
Verifier 收据存在、Provider Audit 完整、客户端和本次连接资源释放。
接受标签必须是整数 0/1，且与组件标签和 Verifier 布尔结果一致，缺字段 fail-closed。

整体工程 PASS 仍要求 T1–T5 按冻结顺序、原 Worker 安排、各一次 Source Attempt
全部完成，且最终连接审计通过。单独报告 successes、valid_failures、censored 和
成功率；不要求五条都成功，也不强行要求五条内同时出现 0 和 1。
不得以新名称输出“任务全部成功”或“正式采集已就绪”。

审查目录中的非生产候选判定函数已检查真实 T1 记录：它可满足新的单任务工程条件，
task_success 仍为 false、标签仍为 0；由于只有一个任务，五任务验收仍不完整。
该反事实检查没有写回原 Run，也不构成新的真实 Gate。

## 修订版本与必要验收

启用前将两项变化作为一个明确版本修订审批：Prompt、Observation 编码和工程
验收标准影响冻结输入，不能只重算旧 Manifest。保留当前 v1 配置/清单与旧 Run，
新增版本配置和清单，并把新提示/编码身份同时绑定 Source、Checkpoint 和 Restore。
Checkpoint Schema 仍保持 2.0.0；已有 Checkpoint 只能使用原配置恢复，不追溯补提示。

必要的离线验收包括：

1. object/array/boolean/数字外观 string/Unicode/首尾空白/换行的工具到实际模型输入
   字节及类型保真；不得仅测试 JSON 编解码函数。
2. 全部 160 个任务的参考执行和公开契约兼容性；公开输入不得包含 expected、
   hidden reference 或故障结果。
3. Fake SDK 完整 Source→首个合格捕获→独立 Restore→resume→Verifier；六项 Hash、
   首次输入、源/分支隔离、剩余预算、审计与清理仍成立。
4. 有效 0 不阻断下一固定任务，有效 1 正常继续；null、Hash/输入/Replay/审计或
   清理失败立即停止；T3 更新后的合法新 ID 重读仍可执行。
5. 旧版本回归保留原“wrong_final 停止”的期望；新版本使用独立期望。历史清单和
   原 0、BLOCKED 不变，五任务没有运行完不能报 PASS。

## Commands and Tests

私有审查目录：
`runs/phase2_continue_contract_audit/p2_continue_contract_audit_20261127_d0835de0/`。
保存 baseline.json、审查测试源文件、测试日志、原清单检查、逐任务参考验证、
Gate 草案反例、T1 对照、T2 Mock 实际请求检查及最终完整性检查。

测试由复制到新目录的 offline_test_guard.py 启动，阻断 socket connect、DNS、sendto、
bind 及 ssh/openssl/curl/wget 子进程。最终命令：

```bash
.venv/bin/python runs/phase2_continue_contract_audit/p2_continue_contract_audit_20261127_d0835de0/offline_test_guard.py \
  runs/phase2_continue_contract_audit/p2_continue_contract_audit_20261127_d0835de0/test_contract_audit.py \
  tests/test_continue_acceptance_entry.py tests/agent/test_continue_outcome.py -q
.venv/bin/python -m compileall -q src scripts tests \
  runs/phase2_continue_contract_audit/p2_continue_contract_audit_20261127_d0835de0/test_contract_audit.py
git diff --check
```

复核时须将守卫和私有测试复制到新的唯一审查目录再运行；产物采用独占创建，
上述命令记录本次实际执行，不应在已发布的审查目录中重复执行或覆盖证据。

最终测试结果和完整性检查如下。私有测试用于本轮证据审查及未启用草案，
没有安装到生产入口，也不作为新增正式实验。没有修改生产代码，因此未重复全仓
843 项测试；所选原有 35 项包含完整 Fake 五任务、有效负例、基础设施中断和三态结果。

过程中首次测试命令写错 outcome 测试路径，pytest exit=4，未运行测试；更正为
tests/agent/test_continue_outcome.py 后原有 35 项及首批审查 182 项共 217 项通过。
后补的 T2 可观测性测试首次错误地对消息 content 的 Python repr 做多行子串断言，
出现一次测试断言失败；改为读取实际 content.text 后重新验证。原始日志均保留，
没有修改生产实现或历史期望以让测试通过。

| 最终检查 | 结果 |
| --- | --- |
| 私有审查/参考任务/未启用草案测试 | 183 passed |
| 原有冻结入口及 CONTINUE Outcome 回归 | 35 passed |
| 最终合计 | **218 passed，0 failed，0 skipped，15.88 秒** |
| 全部参考任务 / reset | 160 / 160 通过 |
| 原真实 Run 清单 | 71 / 71 文件长度和 SHA-256 匹配 |
| 本轮前既有 Run 文件 | 9,145 / 9,145 SHA-256 未变化 |
| 本轮前 Git 跟踪文件 | 342 / 342 SHA-256 未变化 |
| 父清单 / 入口清单 | 校验通过 |
| compileall / git diff --check | 通过 |
| 脱敏报告敏感信息扫描 | 0 命中 |

最终测试记录为 test_results_04.json，完整性结果为 final_integrity.json。
独立审查 Artifact Manifest 的 973 个文件长度和 SHA-256 亦全部校验通过。
上述数量指最终一次验证；此前的命令错误和一次审查测试断言失败仍在原日志中保留。

## Remote Environment Work / 资源使用

本轮实验 SSH、隧道、远端命令、TLS、HTTP、Completion、GPU 和 vLLM 操作均为 0。
网络守卫阻断一次 urllib3 导入时的本地 IPv6 支持检测 bind；没有外部连接，
不把这次被拒绝的本地 bind 隐瞒为“没有任何 socket 操作尝试”。
本地仅新建审查/临时任务环境、文件及 SQLite 数据库；没有触碰已有 Docker 或
远端服务，也没有创建需要远端清理的资源。GitHub 交付单独报告，不计作模型实验。

## Files Changed / Git 状态 / Remaining Work

本轮版本库仅新增本报告；私有原始审查产物位于 Git 忽略的 runs 目录。
Git 自动交付遵循 AGENTS.md 第 15 节，仅提交脱敏报告，测试脚本、原始记录、
凭据、证书和任务环境不上传。

本轮授权落实为离线审查和可检查修订方案，没有启用修订或重跑真实模型。
AGENTS.md 第 4 节要求涉及研究设计的修改“修改前必须说明原因并取得用户确认”；
现有输出 Prompt/输入呈现及验收标准已冻结，应审核上面的具体新版本方案后再启用。
未确认前保持原协议、标签定义和真实 Gate 状态；不进入正式采集。

## Status

离线审查完成；新版契约/工程 Gate 待审核启用。
历史真实状态保持 CONTINUE_ACCEPTANCE_BLOCKED，accepted_y_continue=0。
