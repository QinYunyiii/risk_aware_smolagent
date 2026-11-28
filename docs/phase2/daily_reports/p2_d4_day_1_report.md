# P2-D4 Day 1 Report

## Objectives

建立独立、默认拒绝的 Train-only 读取保护，重新核验资格、Source attempts、主样本及
决策时刻来源。只完成 Day 1；不激活 v2、不回填、不训练、不采集、不进入 Day 2。

**Status: PASS。** 本轮验收使用用户确认的严格边界：授权 Train 输入重新验 SHA-256；
其他历史文件仅检查元数据和既有 Manifest，不读取其字节，不宣称全量历史 Hash 复验。

## Initial State and Git

- 初始 HEAD：`ac8a775fd926b1be6c2af529d4684ad089da29be`，工作区干净。
- 已阅读 AGENTS、PROJECT_GUIDE、README、最新 P2-D3 报告、v2 候选和正式 Logistic 报告。
- 原特征仍为 formal-decision-features-v1，18 个 Allowlist 字段。
- 原 Logistic L2/C=0.1、3 折 template-grouped/seed20261128、阈值0.5 不变，LightGBM 未启用。
- 实现测试通过后冻结实现 Commit：`686deef029768ace98005d35da513387153ff4c1`。
- 正式离线核验从该 Commit 的干净工作区启动；本日报随后单独追加交付。

## Data Sources

专用 Train proof（虽然父目录名称含 validation_stage_a，文件本身仅为 Train 索引）：
`runs/phase2_validation_stage_a/validation_stage_a_20261128_32d73e67/train_proof.json`。
SHA-256：`04c30487e661cd378c3375efd995ae71a6f6a9dc14f287944b1097eb0a350472`。
不使用混合快照，也不打开同目录的其他历史结果。

| 已验证来源 | SHA-256 |
| --- | --- |
| 父 Train Manifest | 8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00 |
| 子 Train Manifest | bf5b52442f49a1c101c5ac3b2eb9c7963ae6d4a49cc9f40449e59da4380fd957 |
| Train Eligibility Release | 05dd527b55a51cdb82e1b01ab0e886eb11811893c31d1259ce07ab906eca1c63 |

原始文件来自正式父/子 Train Run。仅加载 release 指向的 95 个主 risk records，
标签沿用其已发布 label；不打开未来 CONTINUE/Verifier 去重新判标签。
Tasks 记录只用于 Source Attempt 身份及记录链检查，不把其完成后 Usage 等字段用作特征。

## Local Environment Work

全部运行于本地 WSL，使用现有 Python 3.12 环境，没有安装依赖或请求管理员权限。
入口强制 `-I -S -B`：隔离环境/用户启动路径，不运行 site/.pth，禁止 bytecode 写入。
新标准库引导模块直接加载，不先执行原 prediction 包的 eager imports。
不使用任何 API Key；不输出或记录环境凭据值。

## Remote Environment Work

SSH、TLS、HTTP、Qwen、GPU、vLLM、Docker 操作均为 0。没有远程进程或隧道需清理。
GitHub Push 是验收后的代码交付行为，与审计进程的禁止网络边界分开记录。

## Implementation

新增独立 ReadPolicy，不修改 P2-D3 Hook、TrainReader、v1 特征、预处理或模型实现。

- 对数据及支持文件按精确路径登记，不再默认放行 runs 外路径，不按目录或扩展名直接授权读取。
- 项目支持代码来自 Git 索引和明确的新入口文件；标准库代码逐文件建立支持清单。
  本日不添加虚拟环境依赖目录，也不允许读取其中任意数据文件。
- Python 对缺失 bytecode 的探测只允许推导出的精确缓存路径；缓存前缀在本次全新目录内，
  `-B` 禁止创建缓存。可探测路径不代表实际存在或读入了文件内容。
- 引导阶段仅进行已登记代码/标准库支持清单及历史文件 stat；数据读取在保护安装后开始。
- 先验证固定 profile/proof/Manifest，再登记资格记录引用的精确来源；分析前封闭输入表。
  封闭后不能登记新数据，输入和支持映射不可原地扩展。
- 拒绝路径穿越、数据路径符号链接及路径逃逸、未知 FD、未登记读取、历史文件写入、
  删除/重命名/硬链接/符号链接操作、网络和子进程；输出只允许预先声明文件的独占创建。
- 拒绝事件即使被调用方捕获，也会阻止 PASS。输入 Hash 不匹配同样污染验收状态。

原包初始化会引入 sklearn/网络库等非本日所需依赖。初次无数据预检已如实 BLOCKED，
没有因导入需求放宽文件/网络权限。最终运行时只使用标准库及冻结纯函数：

1. Hash 固定原 canonical_json/canonical_hash 源文件，提取其原 AST，不改函数体。
2. 完整复用原 formal_features 和 train_evidence 模块的代码。
3. 从原 formal_preparation 源码提取原 ERROR_TYPES/feature_projection AST。
4. 在当前审计进程中装配所需命名空间，跳过无关包初始化；不重写旧文件、不激活模型。

四个复用源码 Hash 固定在新 profile 中，漂移即停止；专项测试确认纯运行时不导入
numpy/sklearn/smolagents/openai/urllib3，canonical hash 与冻结定义一致。
这属于可信 Python 审计进程的读取纪律，不声明为对恶意原生代码的操作系统沙箱。

## Files Changed

- `configs/p2_d4_day1_access_v1.json`：独立 Day 1 profile，固定 proof、基线、原配置及复用源码 Hash。
- `scripts/audit_p2_d4_day1.py`：隔离启动、读取保护、干净 Commit 与输出审计。
- `src/risk_agent/prediction/train_only_access.py`：默认拒绝的 ReadPolicy。
- `src/risk_agent/prediction/train_day1_runtime.py`：冻结纯代码的导入装配。
- `src/risk_agent/prediction/train_day1_audit.py`：资格、任务记录链、重复元数据及 Hash 核验。
- `tests/test_train_only_access.py`：29 项新增专项测试。
- 本日报。

## Artifacts Generated

正式离线 Run：`p2_d4_day1_20261128_778a842c`。
目录：`runs/phase2_p2_d4_day1/p2_d4_day1_20261128_778a842c/`。

- summary.json：实际统计、来源核验结果及 Gate。
- access_policy.json：585 个精确授权输入、支持文件/探测路径和声明输出。
- access_audit.json：允许读取计数、拒绝事件。
- run_context.json：执行 Commit、argv、profile 和实现文件 Hash。
- integrity.json：历史元数据保护范围及结果。
- artifact_manifest.json：以上五个文件的长度与 SHA-256，独立复验全部匹配。

Profile SHA-256：`6d0fce4398958f90df3642230ea5b4c2acede2b7a0e465b7003a9a243527cc65`。
测试、实现检查及交付核验另存本轮独立的 `runs/phase2_p2_d4_day1_delivery/` 目录。
原始数据、个体记录、模型、完整审计日志均留在本地。

## Commands and Tests

```bash
# 专项和 P2-D3 兼容回归；实际受本轮离线测试监督器保护
.venv/bin/python -m pytest -q tests/test_train_only_access.py \
  tests/test_train_only_signal_audit.py tests/test_train_public_signals.py

# 正式离线核验：已提交、干净实现；无真实 Agent/模型执行
.venv/bin/python -I -S -B scripts/audit_p2_d4_day1.py \
  --profile-sha256 6d0fce4398958f90df3642230ea5b4c2acede2b7a0e465b7003a9a243527cc65 \
  --run-id p2_d4_day1_20261128_778a842c

.venv/bin/python -m compileall -q scripts/audit_p2_d4_day1.py \
  src/risk_agent/prediction/train_only_access.py \
  src/risk_agent/prediction/train_day1_runtime.py \
  src/risk_agent/prediction/train_day1_audit.py tests/test_train_only_access.py
git diff --check
```

复现必须指定新的唯一 Run ID，已有目录禁止覆盖。

**74 passed，0 failed，0 skipped**（29 项新增 + 45 项原专项/兼容测试）。
最后一处 Manifest 路径正则调整后，新增专项再次 **29 passed**；没有扩大模型或真实实验范围。
compileall、差异空白检查和敏感信息扫描通过；未重复全仓测试，旧生产模块未修改。

真实隔离进程测试覆盖：授权数据/支持代码/输出正常可用；runs 内外的未授权文件、
复制到文档目录的数据、伪装为 .py 的文件、混合 validation.jsonl、未来 Outcome 和隐藏状态均拒绝；
符号链接交换、.. 穿越、写入输入、覆写输出、未知 FD、删除、网络与子进程均被阻止。
同一拒绝被 try/except 捕获后 assert_clean 仍失败；Hash 错误和封闭后登记也不能通过。

测试监督器阻止了依赖导入的本地 IPv6 bind 探测；这不是成功建立连接。最终纯代码运行时
预检与正式 Run 均无该探测，正式 Run `blocked_events=[]`。
一次并行预检还检测到测试日志仍在写入，按 BLOCKED 保留；之后串行预检通过。
这些无 Train 数据预检均保留原始状态，没有改写成 PASS，也不是 Source/Qwen 自动重试。

## Quantitative Results

| 项目 | 本次实际核验 |
| --- | --- |
| Source Attempts | 96，唯一 task_id，每任务 source_attempt_index=1 |
| 合格主样本 | 95 |
| CONTINUE 成功 / 失败 | 85 / 10 |
| 模板 | 48 |
| 删失 | 1，与合格样本不交叉，两者恰覆盖全部 Source Attempt |
| 重复分支 Manifest 引用 | 19，全部关联合格 Checkpoint，未增加训练行 |
| 原 18 特征 | 95 行逐项重算一致，provenance 一致 |
| Manifest 元数据条目 | 5318，仅索引元数据 |
| 原 Train 决策来源文件 | 577 个验 Hash |
| 总授权输入 | 585 个，含额外 Tasks、proof、Manifest、profile 和原配置上下文 |
| 正式运行拒绝事件 | 0 |
| Validation/Test、未来结果、隐藏状态文件读取 | 0 |
| 模型训练 / 新采集 / v2 激活 | 0 / 0 / 0 |

## Data Integrity Audit

585 个授权输入在来源校验及结束重验时均与固定 SHA-256 匹配；原始 Tasks/risk/Checkpoint
记录链、Agent/next-input Hash、事件截止点及特征来源 Hash 均通过。
原 v1 提取器、Allowlist、预处理器、模型配置和原 Logistic Manifest 均保持不变。

**47,959 个历史文件的大小/mtime 未变化。** 按用户选择，不对未来/隐藏/其他 Split 文件
重新读取字节算 Hash。因此不把此检查写成“所有历史文件的内容 Hash 均重新验证”。

重复分支证据分层：

- 本次新核验：可信 Manifest 有 19 个不同的 attempt_002 分支 outcome 路径，CP 均属于资格集；
  没有打开对应 outcome 内容，训练行始终只有 95。
- 历史资格结论：已发布 Train continuation 审计确认 19 个有效重复，18 同标签、1 不同；
  本日保留并披露，**没有重新审查分支完整性或重算这 1 个标签差异**。
- 不修改差异标签、不重跑历史任务、不把重复分支作为独立样本。

## Acceptance Checklist

- [x] Run/Manifest/release 身份与授权输入 Hash 核验通过。
- [x] 95 个主样本、85/10 标签、48 模板与资格索引一致。
- [x] 1 个删失保持原样，未作为训练行。
- [x] 19 个重复分支元数据与既有资格结论一致，未计为独立样本。
- [x] Validation/Test、混合快照、未来和隐藏状态真实打开测试被拦截。
- [x] runs 外默认允许的缺口由独立默认拒绝策略关闭。
- [x] 路径逃逸、符号链接和未知 FD 拒绝。
- [x] 历史保护满足用户选定范围，无原始文件修改。
- [x] 74 项专项/回归与编译、空白检查通过。
- [x] 正式核验绑定干净的实现 Commit、唯一 Run、profile Hash 和 Artifact Manifest。
- [x] 没有访问远程、训练、采集或进入 Day 2。

## Known Issues

- 不重新审计未来结果：19 个分支的有效性与 1 个标签不一致沿用历史资格结论，证据层次已披露。
- 其他历史内容未全量重验 Hash，这是明确选择的边界，不是遗漏后默认为通过。
- 支持清单只覆盖当前 Day 1 所需的标准库/纯代码；未来阶段若需要其他依赖，应独立审查并版本化，
  遇到未授权读取不会自动扩大权限。
- Python 读取 Hook 不等于恶意原生代码沙箱；本日范围是受信审计程序的文件读取纪律。
- 研究上的小样本和泛化限制不因本日读取保护 PASS 而消失。

## Remaining Work

等待用户审核 Day 1；之后才可进入 Day 2 候选协议设计。当前 v2 尚未激活，没有 Sidecar，
没有 OOF 新比较，也没有 Train 扩充清单或真实采集授权。

## Status: PASS

仅表示 Day 1 的数据资格与读取保护验收通过，不表示候选特征、模型效能或新采集通过。

## Git Commit

实现：`686deef029768ace98005d35da513387153ff4c1`，正式 Run 使用此版本。
本日报在验收后作为独立文档提交，依据 AGENTS.md 第 15 节一并推送 `origin/main`。
具体文档 Commit 与 Push 结果在最终交付收据/回复报告；原始审计产物不上传。
