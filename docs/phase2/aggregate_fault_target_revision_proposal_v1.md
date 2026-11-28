# 聚合故障目标最小修订提案 v1

**状态：PROPOSED_NOT_APPROVED_NOT_EXECUTABLE。仅供审核，未改变生产行为。**

父代码 `87ff655`；原排程 SHA-256
`1da6c9f8fd5b5fa9142a2f49f743b70adcaa1db99cb3b6119e13e8433f7319da`；
原 Validation 清单 SHA-256
`91b67a83a0073306f60056bf5ce3e657ce2f357e31245ea8589e824cd2d86b5a`。

## 推荐方案

对显式启用新策略的 Data Analysis **F2/F5**，将故障匹配目标定义为聚合操作组：
`{aggregate_data, aggregate_records}`。两种实际工具名、参数、公开 Schema 和结果均不改。
**组内第一次匹配调用注入一次**，后续切换另一工具不再次注入。

这改变故障匹配范围和计数语义，属于需审核的研究设计修订，不按普通重构实施。
不改变 F2 的执行前 503、F5 的执行前可见 no-progress，也不改变捕获时刻、预算或标签。

以下为设计片段，不是可加载的活动配置：

```yaml
status: PROPOSED_NOT_APPROVED_NOT_EXECUTABLE
target_match_policy: aggregate-family-v1
domain: data_analysis
fault_types: [F2_API_503, F5_REPEATED_ACTION]
configured_target_in: [aggregate_data, aggregate_records]
matching_members: [aggregate_data, aggregate_records]
trigger_scope: first_call_across_members
preserve_actual_tool_name_and_arguments: true
```

在新版本的相关 case.parameters 中显式记录 `target_match_policy: aggregate-family-v1`。
无此标志的旧 case 仍采用原精确匹配；未知策略或不支持的 Domain/Fault/目标组合拒绝启动。
组成员表由独立 Manifest 固定，不接受任意扩展。read_csv、filter_* 和写操作不属于此组。

按属性规则检查完整冻结排程元数据，预期影响范围为：

| Split | Task | 故障 |
| --- | --- | --- |
| Validation | data_aggregate_events_count_001 | F5 |
| Validation | data_aggregate_events_count_002 | F2 |
| Test（未执行） | data_aggregate_revenue_sum_001 | F5 |
| Test（未执行） | data_aggregate_revenue_sum_002 | F2 |

Train 没有匹配该规则的 case。此范围按 Domain/Fault/工具组确定，不按失败标签或单独
Task ID 特判。Test 行仅来自已冻结的排程元数据，不涉及 Test 输出，也不授权运行 Test。

## 最小实现边界

新增 FormalFaultInjector 子类，仅覆盖显式策略的 `_should_trigger`；复用原 F2/F5 执行、
错误响应、只读保护、事件生成和状态序列化。不修改通用 FaultInjector 或数据工具。
新 collector 仅在故障工厂选择该子类，并绑定新策略；Source、CONTINUE、Verifier、
18 特征和独立 Restore 继续使用固定原方法。

组内调用序号无需新增持久化计数器。设当前工具为 t，传入的实际工具序号为 i，
已有实际工具计数为 C：

`group_index = i + sum(C[m] for m in members if m != t)`。

该公式排除当前工具，既适用于 F5 增计数前的检查，也适用于基类 F2 增计数后的检查。
F5 未触发时转交基类造成的第二次判定必须得到相同结果，不增加实际计数或注入次数。
保留现有每工具 call_counts 和 FaultEvent.call_index 的实际工具含义；不要改成组序号。
组序号通过策略、已有计数与事件顺序重建，不给 FaultEvent 增加或改写字段。

必须保留以下身份与恢复边界：

- FaultEvent.tool_name、arguments_hash、模型 Tool Call、Replay Signature 仍为实际调用。
- 新策略随已有 fault_identity.parameters 进入 Checkpoint；恢复时要求完全相同。
- 继续使用既有 call_counts、last_results、events、random_state 和 Checkpoint v2 外层结构。
  不修改 Checkpoint Schema；新旧状态的行为语义不能视为可互换。
- 新 Manifest 必须绑定策略表、新 adapter/factory 和入口。旧 loader 即使能读取同形 JSON，
  也不能获准以旧匹配逻辑执行新策略；跨策略/跨 Run 恢复必须在 SDK 前拒绝。
- 策略、组成员和内部计数不进入 Agent 公开错误文本或 18 个风险特征。

拟新增文件（审批后实现，不代表现在已存在）：

1. 新聚合匹配 adapter 与策略表。
2. 新 Validation 修订 wrapper/入口，复用原生命周期方法。
3. 新策略配置、修订 Manifest、本地资格收据及专项测试。

保留原 Manifest、活动计划和所有旧实现；不通过重算旧 Hash 绕过冻结检查，不新建连接、
TLS、状态管理框架。入口需要传播策略修订/hash 到原有身份及产物记录。

## 未选择的方案

| 方案 | 未推荐原因 |
| --- | --- |
| 只将 target 改为 aggregate_records | 模型仍可能选 aggregate_data，盲区方向互换 |
| target=`*` | 会在 read_csv 等更早调用注入，改变故障位置和可见状态 |
| 两个工具各自第 1 次都注入 | 混合路径可能触发两次；恢复后换名仍可再触发 |
| 将两个工具强行合名或互换参数 | CSV 路径与 records 输入不同；过滤后的 rows 不能替换为完整 CSV |
| 改 Prompt、删工具、强制模型调用 | 改变公开工具契约和策略行为，影响大于匹配修订 |
| 在底层 aggregate_records 普遍加注入 | aggregate_data 内部也调用它，容易嵌套注入、错记真实工具身份 |
| 放宽覆盖、改标签、重分类为 F0 | 事后改变验收或原始实验含义 |

## 历史数据和下一次实验

旧 Validation 的 32 主结果、7 重复、26/6 标签、BLOCKED、原 Manifest、旧 cohort claim
全部保留。不得将两个未注入的旧成功结果改为新策略的结果，不发布旧批资格。

建议分两步审核：

1. **仅 Stage A 实施和离线验收**：新版本 adapter/入口/Manifest，真实请求为 0。
2. 审核通过后再单独决定是否授权新修订的 **完整 Validation 32+7**。新实验/Run 和新修订
   claim 命名空间绑定新 Manifest；旧 claim 不删除，也不在旧入口绕过一次性限制。

不推荐按已看到的结果只补两项后与旧 30 项拼接过关，也不按新标签挑选替代行。新旧批次
完整分开，重复 Task/模板的结果不能作为额外独立样本。旧 Val 已用于工程设计，后续报告
需披露这一事实，不能称为从未检查过的独立样本。新策略并不保证模型一定调用组内工具；
若直接根据已读数据返回答案，仍必须记未触发并保留原覆盖门禁。

Train 95 行不改标记或重采。未来分析应显式记录 Train 的旧修订和新 Val/Test 的策略修订；
当前 Train 排程对此组的适用数为 0，不能将此事实夸大为任意未来排程都等价。
Test 继续封存；本提案不授权重采、模型训练、参数选择或 Test。

## 审批后必须通过的本地验收

- 两成员单独路径、两种切换顺序、重复调用，均只在第一次组调用注入；不影响 read_csv。
- F2 与 F5 均不执行被拦截的底层操作，公开响应语义保持原样。
- Source 在首次组调用前/后捕获，独立 Restore 与重复分支保持原计数，不能再次触发。
- 真实工具身份和参数 Hash 原样保留；非法策略/成员/Domain/写操作配置失败关闭。
- 无组调用时不虚构触发；原覆盖 Gate 不降低。
- 旧策略、旧 Fixture、旧数据和 Manifest 不变；新旧配置/Run/adapter 不匹配在 SDK 前拒绝。
- 六类 Restore Hash、首次输入、特征 cutoff/溯源、预算保护、资格发布、资源释放和
  cohort claim 的完整 Fake E2E 通过，再按修改范围执行回归。

本轮私有离线原型只证明匹配和状态机制可行，不是生产实现或真实模型新结果。
