# P2-D3 Train-only Decision-time Public Signal Audit — 2026-11-28

## 1. 目标与结论

**PASS：本阶段审计完成，可以进入下一阶段特征方案设计；没有激活新版特征。**

本轮从 Train 资格证明重建 95 个主样本，不加载混合快照，不读取 Validation 样本、
预测、失败分析报告或 Test，不读取任何未来 CONTINUE/Verifier、隐藏故障或环境状态。
18 个正式字段全部从 Checkpoint 与当时已完成的事件重新计算一致。
审计的成功标准是来源、时间边界、可观测性和研究合理性，不是增加字段数量或提高指标。

主要发现：当前缺陷同时包含 **历史变化不足、冗余、公开语义未表达和样本覆盖有限**。
其中前两项可直接量化；不能用本次描述性审计区分各项对泛化的因果贡献。
全部样本在首个合格 Observation 后捕获，使重复、切换、连续错误等历史字段尚无变化，
但这不证明当前捕获点错误，也不授权移到故障之后。

最优先的公开语义候选仍是“完成状态未知”，覆盖 4 行/2 模板；其中 2 行最终成功、
2 行失败，说明它表达执行状态的不确定性，不能直接充当失败标签。
其余候选多有模板依赖、缺少压力区间样本或与已有错误指示重合，均作条件性建议。

## 2. 范围、Git 与数据来源

开始 HEAD：`c1827f1ced9053ae95d1bd6c81448d6a08c78e42`，初始工作区干净。
用户本次明确要求不自动 Commit，因此 **不 Commit、不 Push**，只新增本任务文件。
没有修改上一轮审查模块、候选方案、既有生产代码、正式 Allowlist、冻结 Manifest 或历史 Run。

独立审计根：
`runs/phase2_train_only_public_signal_audit/train_only_signal_20261128_7787cbfc/`。
最终可复现输出在 `decision_time_audit_delivery/`，此前离线审计输出保留，不覆盖。

专用 Train proof：
`runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/train_proof.json`。
文件所在目录名称来自历史阶段；本轮只打开此单独的 Train 索引，不打开其同目录其他文件。
proof SHA-256：`04c30487e661cd378c3375efd995ae71a6f6a9dc14f287944b1097eb0a350472`。
初始定位 proof 时读取过共同 `source_proof.json` 索引元数据，只使用其 Train proof 路径和 hash；
没有打开混合快照中的 train.jsonl/validation.jsonl。正式可复现入口直接使用独立 Train proof，
不再依赖该共同索引，也不使用其中的 Validation 引用。

| Train 来源 | 冻结 Manifest SHA-256 |
| --- | --- |
| formal_train_20261127_ad34d6d2 | 8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00 |
| formal_train_child_20261128_61c0db85 | bf5b52442f49a1c101c5ac3b2eb9c7963ae6d4a49cc9f40449e59da4380fd957 |

Train Eligibility Release SHA-256：
`05dd527b55a51cdb82e1b01ab0e886eb11811893c31d1259ce07ab906eca1c63`。
release 的每条引用解析至原始 risk_candidates，按原始记录的 label 派生 y_failure=1-label。
本轮允许 Train 标签的描述性分布；没有打开未来结果去重新计算标签，也不重审已发布标签。
原资格审计的真实性与最终结果正确性继续由已发布 release 承担，本阶段不声称重跑了 Verifier。

## 3. 完整性与时间边界

没有调用 prepare_rows、load_snapshot 或 verify_train_prerequisite：前两者可能带入其他 Split，
后者会遍历本轮不需要的完整 Train 产物。新增专用 TrainReader 仅允许明确的决策来源。

1. 验证两个真实 Train Run 的身份、资格 proof/release 和 Manifest SHA-256。
2. 索引 5318 条 Manifest 元数据，拒绝路径逃逸和重复路径；只打开本轮使用的 **577 个文件**，
   分别验证长度和 SHA-256。不声称对全部 5318 个文件重新读内容或重新验全 Run。
3. 验证完整 Train risk_candidates/checkpoints 记录链：previous hash、canonical record hash 和 split。
4. 样本只来自 release；重复 source 引用不添加样本，跨 Run 重复 task/Checkpoint 拒绝。
5. 每个样本核对 CP/风险记录身份、Agent state hash 和 canonical next-input hash。
6. 仅取 CP 内 budget_state/execution_position、公开 Memory、Source model/tool 投影及原 feature packet。
7. 已完成 Source 请求数、Tool Call 数与 CP 预算、Memory 相符；模型 input hash 匹配捕获的 ActionStep；
   工具名/参数匹配 Memory；工具事件时间不晚于该 ActionStep 的完成时间。
8. 18 个值及 provenance 重新计算与风险记录、原 packet 一致，created_at 之外的来源投影逐项一致。

这提供“当时已持有的快照 + 已完成步骤 + 事件对应关系”的证据，不把最终 JSON 中存在的字段
自动认定为决策时刻可用。Source 事件投影文件虽在 Source 关闭后持久化，其值已由上述对应关系
绑定到捕获前已完成请求。它不包含可供本轮分析的 Provider finish_reason，相关推断标记 UNKNOWN。

实际执行安装文件读取保护：只允许两个 Train 根下的公共/Monitor 决策文件及专用 Train proof；
未来 outcome、fault_adapter_state、隐藏 environment、Validation/Test 和其他实验输出均在 open 前拒绝。
最终真实执行 `read_guard_events=[]`，未尝试禁用路径；测试中的故意拒绝另行记录，不算真实数据读取。
对其他历史 Run 只用文件大小/mtime 元数据保护，不读其内容、不作全库 hash 扫描。

## 4. Train 样本复核

资格引用重新得到：**95 行、48 模板、85 个 CONTINUE 成功、10 个失败，1 个历史删失**。
全部捕获于 step=1，一次已完成 Model Request、一次已完成 Tool Call。
重复分支未当作独立样本，删失未补成失败，禁止源与 Fixture 均未混入。

原 18 字段无缺失。每字段完整 unique、范围、均值、总体标准差、value counts、
成功/失败组分布及 48 个模板分布在
[现有特征机器可读表](../audits/train_only_existing_feature_audit_v1.json)。

## 5. 18 个现有特征重新审计

标准差使用总体 ddof=0；常量按唯一值判定，其标准差记精确 0，避免浮点残差误当变化。

| 特征 | 非空 | 唯一值 | 最小值–最大值 | 均值 | 标准差 |
| --- | --- | --- | --- | --- | --- |
| step_index | 95 | 1 | 1–1 | 1 | 0 |
| tool_call_count | 95 | 1 | 1–1 | 1 | 0 |
| observed_history_count | 95 | 1 | 1–1 | 1 | 0 |
| remaining_step_budget | 95 | 1 | 9–9 | 9 | 0 |
| unique_tool_count | 95 | 1 | 1–1 | 1 | 0 |
| repeated_action_count | 95 | 1 | 0–0 | 0 | 0 |
| repeated_action_ratio | 95 | 1 | 0–0 | 0 | 0 |
| tool_sequence_length | 95 | 1 | 1–1 | 1 | 0 |
| observed_error_count | 95 | 2 | 0–1 | 0.1789474 | 0.3833082 |
| latest_observed_error_type | 95 | 5 | 类别 | 不适用 | 不适用 |
| historical_tool_success_rate | 95 | 2 | 0–1 | 0.8210526 | 0.3833082 |
| consecutive_error_count | 95 | 2 | 0–1 | 0.1789474 | 0.3833082 |
| token_budget_fraction | 95 | 26 | 0.0324–0.04838 | 0.03737579 | 0.006395696 |
| tool_budget_fraction | 95 | 1 | 0.08333333–0.08333333 | 0.08333333 | 0 |
| elapsed_time_fraction | 95 | 95 | 0.001069011–0.006471175 | 0.001855302 | 0.0006774933 |
| recent_tool_latency | 95 | 95 | 0.001062736–0.003577246 | 0.001460938 | 0.0004117119 |
| recent_model_latency | 95 | 95 | 0.9200897–5.781823 | 1.631209 | 0.6095863 |
| average_tool_latency | 95 | 95 | 0.001062736–0.003577246 | 0.001460938 | 0.0004117119 |

九个常量是前八项和 tool_budget_fraction。变化列中：

- observed_error_count 与 consecutive_error_count 逐行相同，r=1。
- historical_tool_success_rate=1-observed_error_count，r=-1；这里的“成功率”是无可见工具错误比例，
  不表示最终任务或隐藏状态正确。
- recent_tool_latency 与 average_tool_latency 逐行相同，r=1。
- elapsed_time_fraction 与 recent_model_latency 的 r=0.99982003。

相关性阈值 |r|≥0.95 是本审计的描述标记，不据此删除正式字段；类别没有任意数字编码后计算 Pearson。
常量相关性为 undefined，不赋值 1。常量之间偶然相等和有变化字段之间相等在表中分别标识。

| 决策错误类别 | Train 成功组 85 | Train 失败组 10 |
| --- | --- | --- |
| none | 74 | 4 |
| other | 0 | 5 |
| no_progress | 7 | 1 |
| timeout | 2 | 0 |
| unavailable | 2 | 0 |

既有词表 invalid_argument/conflict 在此 Train 中无样本。风险未必先出现为显式错误：
10 个失败有 4 个在当前捕获点没有可见错误。本轮不打开其未来轨迹解释原因。
错误计数/类别只有 1 个模板存在模板内变化；token_budget_fraction 只有 3 个模板有变化。
时间字段虽有 95 个唯一值，也可能主要表达服务延迟或运行环境，不能据唯一值多认定有效。

## 6. Prompt 信号

只用捕获的用户任务首段；统一输出格式附录不当作任务难度。
先遮盖路径、引号内字面量和标识符，再检查明确的顺序/返回值依赖词，
不把路径里的 timeout、stale、repeated 等任务生成文字当作信号，也不按 ID 或模板名编码。

- 明确返回值/顺序依赖：47 行、24 个模板。它是静态任务结构，所有模板内均不变化，Shortcut 风险高。
- 写入/状态变更意图：13 行；明确要求“使用返回 status 作更新前提”的任务 11 行。
- 明确成功条件：观察到统一的原生 JSON/精确结果要求，但它在统一附录中重复，
  不新增一个机械的常量“有成功条件”字段。

Prompt 能证明文字要求，不能证明隐藏的实际执行计划、难度或唯一正确路径。
目标是否真正模糊、是否存在多条可行路径、跨工具依赖是否必然发生均无法由通用词法规则确认，
标记 UNKNOWN，不从结果反推，不增加 Prompt 长度/词数特征。

## 7. Tool Schema 与 Tool Call

Schema 来自 CP tool_registry；核对工具名称、描述确实出现在当时 system prompt，
调用参数来自 Agent 已发出的 Memory。公共 Schema（A）与 Monitor 可读副本（B）来源一致；
类名/内部适配器状态及隐藏故障配置（C）不进入候选。

95 条调用的必填缺失、多余参数、空值和公开基本类型违例均为 0。所有已发出调用仅一次，
重复/工具切换无变化。该合格 Checkpoint 群体本身要求已进入可捕获步骤，
不能用全零断言真实部署永远没有非法调用。

两条当前调用的公开描述声明状态更新、包含 idempotency_key，来自 1 个模板。
“带幂等键”不证明键有效或写操作已提交；内部幂等/副作用记录被禁止读取。
直接使用工具名、是否带键作为特征很容易成为模板代理，本阶段不推荐。

23 行可按公开 exact ID/SKU 文字与任务中的唯一字面标识符比较，其中 4 行字面不一致，
分属 2 模板；4 行均最终失败，但该关联不用于决定推荐，不能把字面差异等同语义错误。
其一正常观察返回了规范前缀 ID：12 条可比较返回标识符的正常观察中，11 条字面一致、1 条不同。
这提供了“工具可能规范化参数”的公开证据，不允许据 Train 的 4/4 构造确定性标签捷径。
泛化的工具目标匹配、合理数值范围必须有公开语义约束；缺少约束时为 UNKNOWN。

## 8. Observation：公开语义与可执行前提

78 条正常 Observation 均可解析 JSON：array 37、object 15、string 24、boolean 2；
另外 17 条是已收到的显式错误，不误记为正常 JSON null。正常观察没有空值/空容器。
数组长度 1–6，只有 37 行适用，模板内均无变化；没有公开预期数量，不把大小称为异常或不完整。

优先检查错误正文，严格匹配公开工具名及参数封装，忽略参数回显与框架建议：

- 完成状态未知：4 行/2 模板，旧 other 与 timeout 各 2 行；最终成功/失败各 2 行。
- 未知资源：3 行/2 模板，均在旧 other；3 行最终失败。稀疏且不能脱离模板验证泛化。
- 两位与已有类别在本批满足 unknown_resource+completion_unknown=I(other)+I(timeout)。
  因此不同时将两位作为独立新增信息堆叠；这是当前数据描述，不是未来提取规则。

另对 Prompt 明示“状态查询→使用返回 status 更新”的 11 行审查最新观察是否含可用 status：
6 行/3 模板存在该前提缺口，5 行具有 status。84 行不适用，记 null，不能补成 0。
在适用 11 行上，它与 observed_error_count 完全一致；6 条缺口样本最终均成功。
这是合理的执行前提，但当前未证明其比显式错误额外提供风险信息。

工具正常返回不能证明整个任务完成，公开 status/version 也不证明未来行为。
是否部分成功、底层是否已写入、结果是否完整、观察是否足以完成整个任务，
若公开文本和 Schema 不足以确认则为 UNKNOWN。禁止查隐藏环境/未来 Verifier 补全。

## 9. Runtime 与资源

| 信息 | 来源与可观测性 | 本批证据 |
| --- | --- | --- |
| 已用 Token/Tool/Wall 比例 | CP budget + 完成事件；Monitor 可观测 | 原正式字段已表达 |
| 剩余步数 | CP Agent max_steps/step_number；Monitor | 全为 9 |
| 已完成 Model Request 数 | CP budget + Source 请求投影；Monitor | 全为 1，重复现有计数 |
| 单次 Completion 用量比例 | 完成请求 completion_tokens / 捕获 generation.max_tokens；Monitor | 19 个值，0.021484375–0.0546875 |
| 最近 Model/Tool 延迟 | 完成请求/工具事件；Monitor | 与原字段重合 |
| 最终成本/未来延迟/后续错误 | 决策后产生 | 禁止读取/使用 |

Completion 用量只有单次上限 1024 的约 2.1%–5.5%，无接近边界样本。
总 Token 占比约 3.24%–4.84%，Wall 占比约 0.107%–0.647%，Tool 比例 1/12。
没有证据支持“当前捕获时已接近资源耗尽”。单次 Completion 比例可提取，
但只在 2 个模板内有变化，可能主要反映 Schema/输出长度，不作为已成立的压力预测器。

Agent 实际看见的是 Prompt、Schema、调用和响应；精确 Token/延迟/上限账本属于外部 Monitor。
本项目可研究后者，但不能把它写成 Agent 自身已收到的信息。没有 Offline-only 字段被批准为候选。

## 10. 候选清单、冗余与 Shortcut

[候选机器可读清单](../audits/train_only_public_signal_candidates_v1.json) 包含 12 个受审信号，
每项均有定义/公式、精确来源字段、实际 CP record hash 数组、source_records 文件 hash 映射、
可用性/Agent/Monitor 分类、全 Train/按标签/按模板统计、冗余、风险机制和推荐。
`train_template_coverage` 表示可计算/适用模板数，正值模板数另列，不能用 48 个零值覆盖夸大支持。
所有 null 是该规则不适用或没有正常响应，未插补，未作为负样本标记。

| 优先级 | 信号 | 支持与局限 | 建议 |
| --- | --- | --- | --- |
| 1 | public_completion_unknown | 4 正值/2 模板；真实公开错误语义；仍高度稀疏 | CANDIDATE |
| 2 | declared_state_dependency_gap | 11 行适用/6 正值；在适用集与旧错误指示重合 | CONDITIONAL |
| 3 | prompt_explicit_dependency | 47 正值/24 模板；模板内无变化，静态 Shortcut 高 | CONDITIONAL |
| 4 | recent_completion_limit_fraction | 95 可取/19 值；无压力区间，Monitor 可见 | CONDITIONAL |
| 5 | public_unknown_resource | 3 正值/2 模板；与第一项及旧类别代数冗余 | CONDITIONAL，仅诊断/备选 |

另外字面标识符差异列 CONDITIONAL，不优先启用。Schema 违例、空 Observation、数组长度、
已完成 Model 次数、幂等键存在和工具切换本次 REJECT：缺少变化、定义不足或模板代理过强。
REJECT 指本阶段不建议加入正式风险预测器，不否定未来其他群体中可能有价值。

推荐由预定义公开语义及来源/覆盖限制确定，不按 Train 标签单一关联排序。
例如幂等键 2/2 失败也被拒绝，状态缺口 6/6 成功也不倒置定义制造“保护”特征。
没有训练任何模型、计算候选预测 AUC 或使用 Validation 反复筛选。

## 11. 研究问题回答

**RQ1：18 字段为什么不足？** 已确认 9 常量、计数/错误率/延迟冗余、粗错误类别遗漏完成语义，
且 Train 只有 10 个失败。捕获位置限制了动态历史的变化；不能据此确定泛化不佳的单一原因，
也不能声称改变采样比扩样或语义特征更有效。

**RQ2：是否有未利用的公开信号？** 有，最清晰是操作完成状态不确定。
Prompt 返回值依赖和查询后状态前提也有真实公开来源，但前者静态、后者当前高度冗余。
单次 Completion 比例是合法 Monitor 信号，目前无资源压力证据。

**RQ3：是否满足部署？** 公共语义/Schema/Memory 属 Agent-visible 且 Monitor-readable；
使用已完成 Usage、Generation 上限和时间账本的字段属于 Monitor-observable。
不需要故障注入器、最终 Verifier 或私有数据库，来源与部署边界相容。
不代表当前词法规则已适配所有工具错误封装或所有任务语言。

**RQ4：优先哪些？** 只将优先级 1 列为无来源阻塞的 CANDIDATE，2–4 为后续受限研究方向，
5 保留为稀疏诊断备选。不能为满足“3–5 个候选”而把条件项写成已验证特征。

**RQ5：如何补数据？** 需要扩大 Train 独立实例和每个合法公开信号的模板内/跨模板覆盖，
先固定新任务生成、参数、预算、故障分配、重复与停止规则；不按失败结果选择性补样。
现有 CP 足以离线提取第一候选，无需为补字段重采旧轨迹。多次同 CP 分支不等于新增独立样本。
既有 LightGBM 数量门槛是至少 200 行且每类 30，当前尚缺至少 105 行与 20 个失败；
这不是新采集停止规则，也不是泛化或样本功效保证，有效特征/样本量标准还需单独注册。

**RQ6：是否改 Checkpoint？** 目前没有足够证据要求改动。保留首个合格决策点作为固定目标。
多阶段研究是对预先固定的不同阶段分别建样本/目标；动态风险是同一 episode 内随 t 连续更新。
它们都不同于当前每任务一个固定决策点，需要新的采样/预算/相关性和分组设计，
同任务/同模板必须留在同 Split，不能把多个时间点当独立样本随机分割。
禁止事后选故障发生后最有利的点、用当前 Train 失败位置反推采样阶段。

## 12. 下一阶段最小实验建议

先审核一个独立版本的“旧 18 字段 + completion_unknown”候选及来源 sidecar，
只从已存 Train CP 回填。然后另行冻结 Train-only template-grouped OOF 比较，
保持基础模型、参数和阈值不变，比较增量而不同时改变捕获点、预算或故障排程。
增加语义位不是效能保证，样本不足可能使比较仍不可估计或不稳定，必须如实报告。

扩充 Train 可以在候选定义与新批次方案冻结、回填验收后准备启动，不必等模型指标转好。
新增资源使用、真实 Smoke 和采集均须另行授权；本轮不生成新实例、不连接远端。
Validation 不用于本轮设计，Test 保持封闭。当前已有审查历史须保留，不能宣称整个项目从未见过 Validation。

## 13. 命令、测试和独立核验

实际最终审计命令：

```bash
PYTHONPATH=src:. .venv/bin/python scripts/audit_train_only_public_signals.py \
  --train-proof runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/train_proof.json \
  --proof-sha256 04c30487e661cd378c3375efd995ae71a6f6a9dc14f287944b1097eb0a350472 \
  --output runs/phase2_train_only_public_signal_audit/train_only_signal_20261128_7787cbfc/decision_time_audit_delivery

PYTHONPATH=src:. .venv/bin/python runs/phase2_train_only_public_signal_audit/train_only_signal_20261128_7787cbfc/offline_test_guard.py \
  tests/test_train_only_signal_audit.py tests/test_train_public_signals.py -q

.venv/bin/python -m compileall -q src/risk_agent/prediction/train_evidence.py \
  src/risk_agent/prediction/train_signal_audit.py scripts/audit_train_only_public_signals.py \
  tests/test_train_only_signal_audit.py
git diff --check
```

既有输出禁止覆盖，复现必须使用新的唯一 output。语义提取只接收公开白名单投影，
Monitor 接口只接收已核验完成的请求及上限；来源元数据不作为特征。

专项及相关回归 **45 passed，0 failed，0 skipped**，其中本轮新增 26 项。
覆盖合法合成 Train 通过真实加载链、路径及声明 Split 拒绝、Manifest/成员 hash 错误、
记录链损坏、重复来源幂等、晚到事件/未来 step/输入不匹配、隐藏字段/未来来源拒绝、
常量浮点标准差、脱敏 Prompt 不使用路径文字；五个独立进程验证真正的禁用文件/日报 open 被拒绝。
无网络钩子放行的外部请求，没有运行全仓测试，既有生产实现完全未改。

独立检查重新读取仅被允许的 Train 来源，以标准库重算均值/总体标准差、成对相关，
核对候选来源的 CP digest、文件 hash、无泄漏声明和机器表与最终 Run 输出一致。
compileall、差异空白检查及本任务敏感内容扫描通过，原始 Source/未来/隐藏产物不上传。

## 14. 验收表

| 验收项 | 结果 |
| --- | --- |
| Train 来源校验 | PASS，release 与原记录链 |
| Manifest / Hash 校验 | PASS，两个可信 Manifest 与 577 个实际使用文件 |
| Validation 数据读取 | 0 |
| Test 数据读取 | 0 |
| 未来数据使用 | 0；Train 标签取自已发布 risk_candidates |
| 隐藏故障元数据使用 | 0 |
| 18 特征重新审计 | PASS，95 行逐值及 provenance 一致 |
| 候选来源追溯 | 100%，实际 CP record digest 与文件 SHA-256 |
| Agent / Monitor 分类 | 完成 |
| 冗余 / Shortcut 审计 | 完成，含模板内变化 |
| 模型重新训练 / 新增采集 | 0 / 0 |
| 冻结协议修改 | 0 |
| 真实 SSH / TLS / HTTP / Qwen / GPU / Docker | 全部 0 |
| Commit / Push | 均未执行 |

## 15. 交付物、限制与停止点

- 本报告。
- `docs/phase2/audits/train_only_public_signal_candidates_v1.json`：12 项完整审计记录与来源映射。
- `docs/phase2/audits/train_only_existing_feature_audit_v1.json`：18 项全体/标签/模板分布及冗余。
- `scripts/audit_train_only_public_signals.py`：独立 Train-only 入口与进程级读取保护。
- `src/risk_agent/prediction/train_evidence.py`：资格、记录链、文件和时间边界加载。
- `src/risk_agent/prediction/train_signal_audit.py`：统计及固定公开语义规则。
- `tests/test_train_only_signal_audit.py`：来源、隔离、无未来/隐藏数据与统计回归。

本地原始审计、实际 argv、读取保护、测试与最终 Manifest 保留在新目录。
没有删除或覆盖旧文件；历史保护仅校验大小/mtime，未宣称重验全部历史内容 hash。
577 个本轮实际来源仍重验 SHA-256，工作区变化只限上述新增文件，HEAD 保持原值。

限制：稀疏失败和模板支持；仅一个早期决策阶段；规则仅覆盖当前公开封装及英语任务；
没有实际故障/副作用 truth、未来观测或完整性 Oracle；未做因果隔离或泛化检验。
静态 Prompt、参数形状和 Observation 大小可能反映任务构造；Missingness 本身也可能泄露任务类别，
不能在下一阶段无审查自动插补并加入模型。

本轮完成后等待审核，不激活候选、不 Commit/Push、不训练、不采集、不修改 Checkpoint 策略。

## Status: PASS — audit and design readiness only
