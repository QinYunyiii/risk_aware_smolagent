# P2-D4 Day 3 Report

## Objectives

在明确批准的候选协议下，从既有合格 Train Checkpoint 离线回填 v2 Sidecar，
核验新字段来源及原 18 字段不变，不重新执行 Agent/Qwen，不拟合模型，不进入 Day 4。

**Status: PASS。** 实际完成 95 条 Sidecar：4 个 1、91 个 0、0 个 UNKNOWN，
原 18 字段变化为 0，完整 v2 Sidecar 资格独立发布。
此 PASS 仅表示历史离线回填及来源验收通过，不说明特征具有增量预测效用。

## Initial State and Git

- 初始 HEAD：`f06cf2e0e54a3e64bb69c02f28dcefdd1bc69e31`，工作区干净。
- 用户在 Day 2 明确暂停点后回复“继续day3”，授权本候选及 Day 3 历史 Train 回填。
- 授权单独记录，没有修改 Day 2 候选的 PROPOSED 状态或关闭的授权开关。
- 实现及测试通过后冻结 Commit：`ddef10bf07bb256bed755e92731a2f0bd1fe71ff`。
- 正式回填从上述干净 Commit 启动，本日报在验收后追加交付。

## Data Sources

候选 Manifest：
`2517ef42fde46f1a55ba32e6278678dfd170bdd0b44047960fb27c2174f4cd26`。

Day 3 单独批准记录：
`docs/phase2/approvals/p2_d4_day3_feature_v2_approval_v1.json`，SHA-256：
`52752b516d654543c6f05ab63e8b8adc5bbd7409a8dbc0e031623db460c24935`。
范围仅为 historical qualified Train v2 sidecar backfill；模型比较、生产激活、新采集、远程访问均未授权。

执行 Manifest：`docs/phase2/formal_train_features_v2_backfill_manifest_v1.json`，SHA-256：
`60f9fc9adffb8beceff5b32fcd2ab606af3876a964342b9fd00edde9cfb3f4b5`。
它绑定新实现、测试、审批、候选 Manifest 及全部冻结依赖，不改写历史清单。

专用 Train proof SHA-256：
`04c30487e661cd378c3375efd995ae71a6f6a9dc14f287944b1097eb0a350472`。
父/子 Train Manifest 和 Eligibility Release 分别仍为：

```text
8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00
bf5b52442f49a1c101c5ac3b2eb9c7963ae6d4a49cc9f40449e59da4380fd957
05dd527b55a51cdb82e1b01ab0e886eb11811893c31d1259ce07ab906eca1c63
```

仅使用 release 指向的 95 个主样本；1 个历史删失保持原样，19 个重复分支不作为训练行。
没有加载混合快照、Validation/Test、未来 CONTINUE/Verifier 或隐藏故障/环境状态。
旧 Train 标签只存在于资格加载器的原风险记录中，不参与新信号提取，也不复制进 Sidecar。

## Local Environment Work

本地 WSL、现有 Python 3.12，标准库隔离入口 `-I -S -B`。
复用 Day 1 默认拒绝 ReadPolicy 和纯源码运行时，不安装依赖、不运行旧包的训练/客户端初始化。
授权输入逐文件登记、校验后封闭；只允许新目录内声明文件的独占创建。
原 v1 提取器、Allowlist、模型、预处理、Prompt、Verifier、预算和 Split 全部不变。

## Remote Environment Work

SSH、隧道、TLS、HTTP、Qwen、GPU、vLLM、Docker 操作均为 0。
没有需要回收的远程连接资源。GitHub Push 仅为验收后的代码交付，单独记录。

## Implementation

### 冻结协议的独立封装

新模块先检查封装/关联，再调用原 `review_public`，不复制目标短语的生产语义解析器：

- 输入仅包含公开任务、Schema、已完成调用/响应、截止点和已验证标记。
  标签、故障、未来结果、task/template 身份等额外字段不能进入信号接口。
- 显式响应 ID 必须匹配 Memory 调用；多个调用必须有唯一逐响应关联，不能只按数量猜测。
  接收顺序决定最后响应，显式 ID 决定其对应调用。
- 正常 Observation 必须合法 JSON；JSON null/false 或含短语的普通 JSON 字符串均为 0。
  损坏 JSON/NaN 等非 JSON 常量为 UNKNOWN。
- 错误前缀必须匹配实际工具名及参数 repr，错误正文必须单行；参数回显不参与语义匹配。
  只接受空后缀或冻结的框架后缀，未知建议/额外行/多行正文为 UNKNOWN。
- 未知对应或格式保留 UNKNOWN；完整性/Hash 错误直接 BLOCKED，不通过改值掩盖。

提取器版本：`p2-d4-public-completion-backfill-v2.0`。
提取器源码 Hash 固定在执行 Manifest；没有更改 Day 2 已冻结的定义。

### Sidecar 与资格

每个 Checkpoint 生成一条独立记录，共 95 条，封装保存在一个新建 `feature_sidecars.json`
数组中，不是覆盖原文件，也不是新增 95 个 Source Attempt。
每条有独立 `sidecar_record_sha256`，可按 checkpoint_id/task_id 索引。

记录包含 schema、task/template/checkpoint、原 risk/CP record hash、v1 特征 Hash、
公开投影 Hash、提取器版本/Hash、候选/执行/审批 Hash、cutoff、创建时间、
19 项字段及值、来源文件/Hash 和解析诊断。身份与调用 ID 仅用于 provenance，不进入 X。

原 18 值直接继承已重新核验的 v1 packet，顺序严格保留，末尾追加新字段。
Sidecar 不添加 y_continue/y_failure、不修改标签、不向旧 CP 或原风险记录写入。

只有全部来源完整、无 UNKNOWN、历史复核计数一致、无读取拒绝/历史变动时，
才发布本 Run 独立的 `feature_eligibility_release.json`。
资格引用 95 个 Sidecar record hash，范围只限历史 Train v2 特征记录；模型比较与生产激活仍 false。
出现 UNKNOWN 或计数不符时保留 Sidecar 诊断，阻止资格发布，不调规则凑数。

## Files Changed

- `docs/phase2/approvals/p2_d4_day3_feature_v2_approval_v1.json`：独立 Day 3 批准范围。
- `docs/phase2/formal_train_features_v2_backfill_manifest_v1.json`：独立执行清单及 21 个依赖 Hash。
- `src/risk_agent/prediction/train_feature_backfill_v2.py`：公开边界检查、解析复用和 Sidecar 构建。
- `scripts/backfill_train_features_v2.py`：隔离、版本绑定、来源核验、输出与发布门禁。
- `tests/test_train_feature_backfill_v2.py`：43 项新增专项。
- 本日报。

没有修改 Day 1/2、P2-D3、v1 正式特征/模型/配置或任何历史产物。

## Artifacts Generated

正式 Run：`p2_d4_day3_20261128_8b5cfd26`。
目录：`runs/phase2_p2_d4_day3/p2_d4_day3_20261128_8b5cfd26/`。

| 文件 | 内容 |
| --- | --- |
| feature_sidecars.json | 95 条可索引、各自带 Hash 的 Sidecar |
| feature_eligibility_release.json | 本轮合格 Sidecar 引用，未授权拟合 |
| source_proof.json | 专用 Train 资格链与 577 个原决策来源文件 Hash |
| summary.json | 实际/预期统计、资格状态与范围 |
| access_policy.json / access_audit.json | 精确读取策略及实际读取审计 |
| integrity.json / run_context.json | 历史保护、实现 Commit、实际 argv 与执行 Hash |
| artifact_manifest.json | 上述八个成员的长度和 SHA-256 |

正式 Artifact Manifest SHA-256：
`e84261247f8b8cf78b23a8ca63c5c3517fc108278a603780a01187cdc81fd943`。
本地测试、实现检查及独立逐条核验保存在新的 `runs/phase2_p2_d4_day3_delivery/` 审计目录。
原始记录、Sidecar、完整审计和资格索引均留在本地，不上传个体数据。

## Commands and Tests

```bash
# 实际通过本轮独立离线测试监督器执行
.venv/bin/python -m pytest -q tests/test_train_feature_backfill_v2.py \
  tests/test_feature_v2_candidate_contract.py tests/test_train_public_signals.py \
  tests/test_train_only_access.py tests/test_train_only_signal_audit.py

# 正式离线回填，从干净实现 Commit 启动
.venv/bin/python -I -S -B scripts/backfill_train_features_v2.py \
  --execution-manifest-sha256 60f9fc9adffb8beceff5b32fcd2ab606af3876a964342b9fd00edde9cfb3f4b5 \
  --run-id p2_d4_day3_20261128_8b5cfd26

.venv/bin/python -m compileall -q scripts/backfill_train_features_v2.py \
  src/risk_agent/prediction/train_feature_backfill_v2.py tests/test_train_feature_backfill_v2.py
git diff --check
```

已有 Run ID 禁止覆盖，复现必须使用新的唯一 Run ID。

**154 passed，0 failed，0 skipped**：43 个新增 + 111 个候选、旧解析器、读取保护和来源回归。
覆盖正常 JSON/null/字符串、大小写/单词边界、参数回显、未知框架建议、多行或损坏封装、
Call id 冲突/重复、多调用关联缺失、非 Train、未来/隐藏字段、未验证截止点、
审批范围、Sidecar 值/Hash/JSON 往返、UNKNOWN 阻止资格、重复主样本拒绝。
既有回归继续覆盖 Manifest/成员 Hash 错误、真实文件打开拦截及来源时间边界。
没有重跑整个仓库测试，也没有任何测试使用真实模型。

compileall、差异空白检查和敏感信息扫描通过。
一次无数据预检因助手命令中的执行 Hash 填写错误而 BLOCKED，校验阻止了 Train 加载；
保留该原始记录。使用已生成的准确 Hash 后无数据预检通过，规则未改，
正式历史回填只执行一次；没有任何 Source/Qwen 重试。

## Quantitative Results

| 项目 | 实际结果 |
| --- | --- |
| 合格 Train Sidecar | 95 |
| 模板 | 48 |
| completion_unknown=1 / 0 | 4 / 91 |
| UNKNOWN | 0 |
| 原 18 字段变化 | 0 |
| 独立重验 Sidecar record Hash / provenance | 95 / 95 |
| 删失样本 / 重复分支进入 Sidecar | 0 / 0 |
| 已授权输入 SHA-256 重验 | 591 |
| 正式 Run 读取拒绝事件 | 0 |
| Agent 执行 / Qwen / 训练 / 新采集 | 0 / 0 / 0 / 0 |

4/91/0 是实际提取结果，不是按故障、标签、模板或目标计数赋值。
所有 95 条均有整数值；没有将 UNKNOWN 自动补零或删除不利记录。

## Data Integrity Audit

所有授权输入在资格加载和结束时验 Hash；原 577 个 Train 决策来源包含于 591 项授权输入中，
新增部分是执行/候选/审批等授权元数据。无 Validation/Test/未来/隐藏文件读取。
**47,997 个历史文件的大小/mtime 无变化**；其他历史内容不重读字节算全量 Hash，遵守已确认边界。

独立核验在另一个默认拒绝进程中完成，没有调用生产提取函数来验证其自身输出：

1. 检查八个正式 Artifact 成员的长度/Hash，以及 95 个 Sidecar record Hash。
2. 将 Sidecar risk/CP 引用解析到原记录，独立重算其 canonical record Hash 并核对身份。
3. 将全部 18 值、顺序和 v1 Hash 与原 feature packet 比较，95/95 一致。
4. 从原 CP 公共 Memory/消息重建公开投影 Hash，核对 Agent/next-input Hash、截止步与调用 ID。
5. 直接检查原公开错误正文独立统计 4 个声明，正常 JSON 均为 0，与 95 条 Sidecar 一致。
6. 核对资格引用覆盖全部 95 条且无重复；无标签字段混入 Sidecar 或 19 项模型输入。

独立进程验证 600 项授权 Hash（591 项原执行输入加本 Run 自有验收产物），拒绝事件为 0。
这里不重新验证隐藏环境或未来分支的六类 Restore 状态；其既有资格结论继续由原发布链承担。
不会把本阶段的 CP/特征核验误写为重新执行了一次 Restore。

## Acceptance Checklist

- [x] 用户批准与冻结候选 Hash 绑定，范围仅 Day 3。
- [x] 95 条原 Train 记录、文件和截止点来源通过核验。
- [x] 95 条独立 Sidecar 记录生成成功。
- [x] 原 18 字段逐条及顺序保持不变。
- [x] 新字段来自原公开响应，不依赖未来或隐藏数据。
- [x] 4 个 1、91 个 0、0 UNKNOWN，独立复核一致。
- [x] Sidecar Hash、公开投影、CP/risk 引用和 Artifact Manifest 完整。
- [x] 原始文件与历史结果未修改。
- [x] 无 Validation/Test、模型训练、Qwen/Agent 或新增采集。
- [x] 不进入 Day 4，模型比较授权仍为 false。

## Known Issues

- 4 个正值仍仅覆盖 2 个模板，工程回填成功不证明增量信息或泛化能力。
- 19 个历史重复中的标签差异及 1 个删失继续保留，不通过本次回填改变其研究限制。
- 正式输出的 95 条为一个 JSON 数组中的独立 Sidecar 记录，未生成 95 个单独文件；
  每条都有可索引身份和独立记录 Hash，数组整体也受 Artifact Manifest 保护。
- 当前协议只处理规范公开封装；未来未知封装、多调用无法唯一关联等继续 UNKNOWN，不自动降级为 0。
- Day 1 Python 读取保护不是恶意原生代码沙箱；适用范围和历史 Hash 核验边界保持不变。

## Remaining Work

提交 Day 3 结果等待审核。只有再次明确批准后，才进入 Day 4 的配对 Train OOF 比较；
届时须固定相同样本/Fold/预处理/参数、配对模板 Bootstrap，不重新访问 Validation 调参。
当前没有模型比较、正式生产激活或下一批采集授权。

## Status: PASS

仅表示历史 Train v2 Sidecar 回填与资格验收通过，不代表新增特征已获得增量收益。

## Git Commit

执行实现 Commit：`ddef10bf07bb256bed755e92731a2f0bd1fe71ff`，正式回填启动时工作区干净。
本脱敏日报在验收后独立追加，按 AGENTS.md 第 15 节提交并推送 origin/main。
最终文档 Commit 与 Push 结果另存交付收据并在回复中报告，Sidecar/原始数据不上传。
