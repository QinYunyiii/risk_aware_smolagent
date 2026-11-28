# Train Decision-Time Public Signal Review — 2026-11-28

## Executive Result

**TRAIN_PUBLIC_SIGNAL_REVIEW_PASS：可用性与来源审查通过，未评估预测增益。**

仅解析合格 Train 的 95 个决策 Checkpoint、48 个模板。公开任务、工具 Schema、
调用参数和响应均可取得，全部捕获于 step=1。提出一个待批准的最小候选：
保留原 18 字段，增加 `public_completion_unknown` 一位；不修改现有生产特征。

不重训，不按标签或模型分数筛选规则，不计算标签关联；不解析 Validation/Test
样本、预测或未来分支。当前模型效用仍未建立，Test 仍封闭。

## Local Work and Evidence

开始 HEAD：`2c7f13c34d714fd89b40d4f971a8211c2744d914`，工作区干净。
独立审计根：
`runs/phase2_train_signal_audit/train_signal_audit_20261128_74632460/`。
最终结果：`train_public_review_final/summary.json` 与 `public_signal_reviews.json`。

仅打开准备快照的 train.jsonl 和共同索引元数据，不调用会同时加载 Train/Validation 的
load_snapshot。共同 source_proof 中只使用 Train 引用；没有使用 Validation 数据部分。
校验冻结快照 Manifest、Train 文件 hash、Train proof、资格 release 及所有 **5318** 个
Train 源 Artifact。资格引用与冻结的 95 个主记录完全一致，不包含重复或删失记录。
只解析 Checkpoint 元数据和当时 agent_state 的公开投影；Train 未来产物仅按原
Manifest 作字节 hash 核验，不作为语义输入，不解析最终结果。

快照 Manifest SHA-256：
`bd48e1b0b07d83574854e083a67226d47c79c8cbe8fd6cbc494fde76696c5e44`。
Train proof SHA-256：
`04c30487e661cd378c3375efd995ae71a6f6a9dc14f287944b1097eb0a350472`。

公开投影仅包含 split、task_text、tool_registry、calls、responses、cutoff_step。
核对 task_text 确实位于当时 user message，canonical messages hash 与 Checkpoint 一致。
任务 ID、模板 ID 和文件引用只用于来源/覆盖索引，不传给语义审查函数。
标签、隐藏 fault 配置、expected answer、私有环境状态及未来轨迹不跨越该接口。

最终执行监督器禁止打开真实 Validation/Test 数据路径，记录被禁止访问次数为 0；
禁止网络及 SSH/OpenSSL/HTTP/Docker 工具。全局历史完整性检查只计算原文件 SHA-256，
其中包含其他 Split 的不透明文件字节，不解析其内容用于本次审查。

## Public Coverage Findings

| 公开信号 | 有值 / 可比较范围 | 命中行数 | 命中模板数 | 结论 |
| --- | --- | --- | --- | --- |
| 错误正文：未知资源 | 95 | 3 | 2 | 可计算，保留诊断 |
| 错误正文：完成状态未知 | 95 | 4 | 2 | 提出最小候选 |
| Schema 必填参数缺失 | 95 | 0 | 0 | 当前无变化，不新增 |
| Schema 多余参数 | 95 | 0 | 0 | 当前无变化，不新增 |
| 明示标识符的字面差异 | 23 | 4 | 2 | 适用性有限，不直接新增 |

公开错误正文成功解析 17 条，正常 Observation 78 条，无 UNKNOWN。
正常响应均可 JSON 解析：array 37、object 15、string 24、boolean 2。
17 条错误没有正常 JSON Observation，统计为 not_applicable_error，不能误记为 JSON null。
工具 output_type 为 any，不据返回形状认定错误，也不把它加入最小候选。

4 条“完成状态未知”中，旧粗类别 other 2 条、timeout 2 条。
两类错误都公开声明操作是否完成尚不确定；该信息与错误大类存在区别。
3 条“未知资源”均属于旧 other。以上是公开信号交叉表，没有使用最终成功/失败分组。

95 行均满足：unknown_resource + completion_unknown = I(other) + I(timeout)。
因此相对于既有类别展开，两位同时加入至多增加一个线性独立方向。
只建议首先增加完成状态这一位，不堆叠多个近重复稀疏字段；等式不当作未来规则。

字面不一致指：公开 Schema 描述要求 exact ID/SKU、任务首段恰有一个带前缀资源标识符，
而调用参数与它不是完全相同字符串。4 个命中中有 1 条正常返回，没有公开工具错误。
这说明该规则不能直接等同“调用非法”，更不能视为失败标签；不扩大成通用语义校验器。

## Candidate Decision and Limits

候选详见 [Train Public Signal Candidate v2](../train_public_signal_candidate_v2.md)。
状态 **PROPOSED_NOT_APPROVED_NOT_EXECUTABLE**，19 字段建议没有激活。
匹配仅针对与公开调用严格对应的错误正文，参数回显和框架重试建议不算正文信号。
UNKNOWN 阻止合格特征发布，不默认为 0，不改记为 Agent 失败。

选择依据是可用性和最小信息增量，没有计算训练标签关联、模型系数或 OOF 指标。
仅 4 行/2 模板支持，不能据此声称可泛化或能够修复旧 Validation 的误差。
项目已观察过旧 Validation，后续仍须披露探索历史；本次没有新增 Validation 访问。

现有 Checkpoint 可离线回填该位。批准后生成独立带版本 sidecar 并做 Train-only 验收，
旧特征/模型/指标和原始 Run 保留，不需要为补字段重采旧 Source。
Schema、捕获点、标签、预算、Prompt、Verifier 和 Split 本轮均未变动。

## When More Collection Is Needed

**完成本轮审查、确定特征版本并冻结新批次后，可以继续准备扩充 Train；
不需要等模型指标改善，也不应反复等待更多诊断框架。**

先决定保留 v1 或批准候选 v2。采用 v2 时，先离线回填并验收来源/一致性；
模型比较如需进行，另行冻结 Train-only 分组 OOF 方案，不用当前 Validation 追调。
之后按预先批准的新 Train 实例清单、故障覆盖及固定停止规则采集，
不将旧实例重复执行当作新的独立样本，不为凑失败数量选择性保存或临时加任务。

既有 Train 只有 95 行、10 个失败，需要补充样本和信号/模板内实例覆盖。
既有 LightGBM 必要数量条件为至少 200 行且每类至少 30 行，当前至少缺 105 行和
20 个失败；达数量条件仍不充分，有效特征样本量标准尚待单独注册。
这些数字用于说明缺口，不构成按结果停止采集的规则，也不自动启用 LightGBM。

下一批清单须明确新增实例生成和内容去重、Train 模板边界、重复分支、seed、
Source Attempt/预算、版本兼容及异常停止规则。批量前按仓库规范重新检查资源与服务、
执行获批准的 Smoke；这些不是本轮已执行事项。当前尚无新的正式批次获授权。
Validation/Test 不用于挑选这批实例；Test 等模型和评估协议冻结后再进入。

## Commands and Tests

```bash
# 实际最终入口由本轮独立监督器执行，额外禁止真实 Validation/Test 文件访问。
PYTHONPATH=src:. .venv/bin/python \
  runs/phase2_train_signal_audit/train_signal_audit_20261128_74632460/final_train_only_runner.py

# 实际 CLI 参数。目录已存在；独立复现须选择新的唯一输出目录。
PYTHONPATH=src:. .venv/bin/python scripts/review_train_public_signals.py \
  --snapshot runs/phase2_baseline_preparation/baseline_preparation_20261128_b841be51/qualified_snapshot_final_20261128 \
  --output runs/phase2_train_signal_audit/train_signal_audit_20261128_74632460/train_public_review_final

.venv/bin/python runs/phase2_train_signal_audit/train_signal_audit_20261128_74632460/offline_test_guard.py \
  tests/test_train_public_signals.py tests/test_formal_error_audit.py -q
.venv/bin/python -m compileall -q src/risk_agent/prediction/train_public_signals.py \
  scripts/review_train_public_signals.py tests/test_train_public_signals.py
git diff --check
```

最终专项及相关回归 **31 passed，0 failed，0 skipped**，其中新增 19 项。
测试覆盖公开正文/参数回显区分、超时与写操作共用完成语义、普通 JSON 不误判、
错误格式 UNKNOWN、字面差异非标签、缺失参数、JSON null 与错误不适用区分、
禁止私有字段/身份/其他 Split、来源边界及重复样本拒绝。
测试网络钩子拦截库导入时的本地 IPv6 bind 探测；没有实验网络连接。
compileall、diff check、敏感内容扫描通过。未运行全仓测试；既有生产模块无修改。

## Artifacts, Git and Resources

本地保存开始 Git/历史文件 hash、资格审查、初次/最终审查结果、实际 argv、
Split 访问审计、测试、历史完整性及统一 Manifest。初次 JSON 类型汇总将“不适用”
显示为 null，最终汇总区分该状态并加测试；初次离线输出保留，没有改写。
对初始所有跟踪文件和历史 Run 文件重新逐项验 hash，均未改变，数量见完整性记录。

Git 仅新增分析模块、CLI、测试、候选说明及本报告；原始观察、个体索引、
模型和审计目录不上传。验收后依据 AGENTS.md 第 15 节自动 Commit/Push。
实验网络请求、远端操作、Qwen、GPU/vLLM/Docker、正式拟合、轨迹采集均为 0；
GitHub 交付网络单独记录。工作区无本任务之外的改动。

## Remaining Work

待审核候选及下一批 Train 计划；本轮不激活特征、不拟合模型、不生成/采集新任务。
采集应进入新的固定批次，不以改变旧负面结果为目标。审核前原 18 字段基线继续有效。

## Status: PASS — availability review only
