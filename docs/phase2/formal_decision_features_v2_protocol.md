# P2-D4 Day 2 — Feature v2 Candidate Protocol

Revision: `p2-d4-public-completion-unknown-candidate-v2.0`。
Status: **PROPOSED_NOT_APPROVED_NOT_EXECUTABLE**。

本文件冻结待审核的候选内容，不激活特征，也不授权 Day 3 回填、Day 4 比较或正式采集。
原候选说明、v1 代码和历史结果保持原样；本文件与独立 JSON 配置由新 Manifest 绑定。

## 1. 字段与研究含义

新 schema 为 `formal-decision-features-v2`，严格保留原 `NAMES` 的 18 项名称、顺序和取值，
仅在末尾增加 **public_completion_unknown**，共 19 项。

该位检测：当前决策点，最后一条模型已接收的工具响应，其可明确解析的公开错误正文
是否包含规范的“completion status is unknown”声明。
这是公开完成状态不确定的信号，不是底层提交真值、失败标签或 Retry/Replan 建议。
不承诺提高预测指标；覆盖稀疏和模板依赖的限制继续保留。

定义（仅在来源、截止点及对应关系已经核验的前提下）：

| 条件 | 诊断值 |
| --- | --- |
| 正常 Observation 的封装和 JSON 均有效 | 整数 0 |
| 可解析公开错误正文匹配 `\bcompletion status is unknown\b`，忽略大小写 | 整数 1 |
| 可解析的其他公开错误正文不匹配该规范声明 | 整数 0 |
| 封装、正文边界或调用/响应对应关系不能确认 | 字符串 UNKNOWN |

只接受整数 0/1，拒绝 bool、浮点、字符串数字和 null 的自动转换。
UNKNOWN 保留诊断和原始来源，不能插补、转为 0、改标签或静默丢弃后发布“完整 95 行”。
任何 UNKNOWN 都阻止完整合格 v2 数据集发布；源 Hash 不符等完整性错误直接 BLOCKED，
不能用 UNKNOWN 掩盖来源损坏。正常 JSON 的 null 是合法返回值，与诊断 UNKNOWN 不同。

## 2. 决策时刻来源

权威来源为已验证 Train Checkpoint 的 Agent Memory 与 canonical received tool responses，
截止点保持 `after_tool_observation_before_model_generation`。
只用 Agent 已收到的最后一条工具响应，Monitor 也可读取同一内容。

关联证据须包含 Checkpoint、原 risk record、原 feature packet、公开投影及相应文件 Hash。
task_id/template_id/Run ID/调用 ID 仅作来源索引，不进入模型。
不读取最终 CONTINUE、Verifier、未来响应/成本、故障类型/目标/触发时间、私有数据库或副作用 truth。
不从任务路径、模板名称、故障分配或最终成败推算字段值。

单条工具响应与对应调用的关系：

- 使用捕获 Memory 中已发出的工具调用，必须已完成且位于截止点之前。
- 若响应显式携带 Call id，必须与该 Memory 调用 ID 精确一致。
- 没有响应 ID 时，只在已完成 ActionStep 内调用/响应能够唯一对应时接受；
  本协议针对单调用单响应可以使用已验证的捕获顺序。
- 多调用情形必须存在显式且唯一的逐响应关联，数量相等本身不构成关联证据。
  缺失、冲突、重复或错序导致无法确认“最后响应对应哪次调用”时为 UNKNOWN。
- 对应关系检查使用 ID 作为 provenance；不把 ID 值编码成风险特征。

## 3. 错误正文边界与解析复用

Day 3 应在独立 v2 封装中先检查上述关联/封装，再复用
`train_public_signals.py:review_public` 的已有公开错误解析。
不重写第二套完成语义解析器，不修改已有模块或 v1 映射。

正常响应要求以精确 `Observation:\n` 封装开头，后续内容能解析为 JSON；
JSON 字符串即使含有目标短语也返回 0。损坏 JSON 为 UNKNOWN，不能沿用旧审查器的零值。

错误响应接受已冻结形式：可选的已核验 `Call id: ...` 行、`Error:` 行，以及单行
`Error executing tool '<name>' with arguments <repr>: <ExceptionType>: <message>`。
name 和参数 repr 必须与对应公开调用精确匹配；异常类名称只用于分隔，不作预测变量。
目标短语仅在 `<message>` 内匹配，参数回显中的相同文字不参与。

正文之后只接受空后缀或已知框架后缀：

```text
Please try again or use another tool
Now let's retry: take care not to repeat previous errors! If you have retried several times, try a completely different approach.
```

框架建议不算错误正文，不能因其包含目标短语判为 1；若后缀被改写或无法识别，
则封装未知、返回 UNKNOWN。多行错误正文或额外未知行同样 UNKNOWN，不截掉未知内容猜测 0。
该版只声明支持当前规范英文短语与封装，不做同义改写、否定句或提交状态的自然语言推理。

已有审查器没有完整检查响应 ID、JSON 合法性及尾部封装，因此**不能裸调用它就发布 v2**。
这些是独立 v2 调用前置条件；Day 2 仅冻结要求，尚未实现或验证真实回填入口。

## 4. 版本隔离与批准方式

独立配置：`configs/formal_decision_features_v2_candidate.json`。
独立候选 Manifest：`docs/phase2/formal_decision_features_v2_candidate_manifest.json`。
独立只读合同检查模块：`train_public_signals_v2.py`，不提供特征提取、回填或拟合 API。

原 formal_features.py、formal_preparation.py、formal_logistic.py、v1 Logistic 配置及历史 Manifest
全部不修改；旧 18 字段和历史负面结果继续有效。

配置中的生产激活、历史回填、模型比较、新采集授权全部为 false。
用户审核时应引用候选 Manifest SHA-256；后续批准记录另存，不能修改此冻结候选来伪装已获批准。
批准 Day 3 回填不等于批准 Day 4 模型比较，更不等于批准正式采集。

## 5. Day 3 预期验收与停止条件（本日未执行）

历史 Train 预期：95 个合格主样本，4 个 1、91 个 0、0 个 UNKNOWN，原 18 字段变化为 0。
这些数字来自先前已发布的审查，是将来复核目标，不是解析规则或未来批次分布约束。
若不一致，保留实际结果并停下排查，不修改规则凑数、不重跑 Agent、不重标标签。

批准后只生成独立 sidecar，绑定旧记录/文件及新公开投影/提取器版本的 Hash，
保留 task/template 身份作来源关联，记录 cutoff 和创建时间，不写回旧 CP 或 feature packet。

Day 3 必测：正常 JSON、合法 JSON null、普通 JSON 字符串含短语、规范错误大小写、
参数回显/框架建议含短语、未知后缀、多行/损坏封装、调用 ID 不匹配、多调用关联缺失、
非 Train、未来/隐藏字段和 Hash 错误。UNKNOWN 不得变为 0，任何完整性差异必须 fail closed。

## 6. Day 2 停止点

本日交付合同、配置、Hash 与专项测试，只说明“候选定义已固定且隔离”，
不说明新字段已在真实 Checkpoint 上回填成功或具备预测增益。
提交协议供用户明确批准；审核前不进入 Day 3。
