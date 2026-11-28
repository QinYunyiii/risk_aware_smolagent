# P2-D4 Day 2 Report

## Objectives

冻结一个独立、未激活的 Feature v2 候选协议：保留原 18 字段，仅追加
`public_completion_unknown`。固定来源、调用/响应关系、UNKNOWN 和版本隔离规则，
交付候选配置及 Hash，等待明确批准后才进入 Day 3。

**Status: PASS — candidate definition only。**
候选内容已固定，不代表获得执行授权、完成真实回填或证明预测增益。

## Initial State and Git

- 开始 HEAD：`fa85bd4b2556c2d55185a7590a2511ca445f93da`，工作区干净。
- 已阅读 Day 1 日报、原 v2 候选、现有公开解析器、v1 字段及冻结依赖。
- Day 1 Gate=PASS；其五个 Manifest 成员的长度与 SHA-256 在本轮作为前置元数据复验一致。
- 仅新增候选文件；不改 Day 1/P2-D3 代码、原候选说明、v1、训练配置、历史模型或 Run。
- 候选实现及合同检查测试通过后冻结 Commit：`8f08923a9281cf03b95ca961f1dee43a3c9fb06a`。
  随后的只读候选预检从该提交的干净工作区执行。

## Data Sources

只读取候选定义、冻结源码及 Day 1 验收元数据，不加载 Train/Validation 混合快照，
不打开真实 Train Checkpoint、Tool Response、未来分支、Validation/Test 样本或预测。

Day 1 前置 Run：`p2_d4_day1_20261128_778a842c`。
其 Artifact Manifest SHA-256：
`60d61ce9137730338800caa7c5d1ac6588f422d06bf37afc45349c37966c4ce2`。

历史 95 行、4 个正值、91 个零值、0 UNKNOWN 只作为 Day 3 的待复核目标，
本轮没有重新提取或重新统计这些值，也不以这些计数决定解析结果。

## Local Environment Work

本地完成协议/配置、只读候选合同校验和合成测试。
合同模块只使用标准库；不导入旧 prediction 包初始化所带入的训练库。
候选预检使用 `-I -S -B`，复用 Day 1 ReadPolicy，登记精确候选/支持文件，
封闭输入后检查合同，禁止未授权读取、网络、子进程和历史写入。

## Remote Environment Work

SSH、TLS、HTTP、Qwen、GPU、vLLM、Docker 均未使用。
GitHub Push 仅用于完成验收后的代码交付，与候选预检的网络禁止边界分开记录。

## Implementation

### 版本与授权

新增独立 schema 名称 `formal-decision-features-v2` 的候选定义，字段顺序为
原 `NAMES` 18 项原样保留，末尾追加 `public_completion_unknown`。
只读合同加载器通过 AST 读取原 NAMES 字面量，再验证 19 项名称和顺序；
不导入或重写 v1 提取器，不把 task_id/template_id/调用 ID 作为特征。

新增模块只提供合同、Hash 和值域校验，没有消息提取、Checkpoint 回填、生产者或模型 API。
候选配置与 Manifest 的状态均为 **PROPOSED_NOT_APPROVED_NOT_EXECUTABLE**。
生产激活、历史回填、模型比较和新采集授权全部 false。

后续批准使用单独记录，引用本候选 Manifest SHA-256；不修改已经冻结的候选文件。
批准 Day 3 不等于批准 Day 4 或正式采集。旧 18 字段基线继续有效。

### 字段语义与 UNKNOWN

来源为截止点之前最后一条 Agent 已收到的公开 Tool Response，Monitor 可读取同一信息。

- 正常封装、JSON 和对应关系均确认有效：整数 0。
- 单行公开错误正文匹配规范英文短语，忽略大小写：整数 1。
- 其他可明确解析的错误正文：整数 0。
- 封装、错误边界、正文或关联关系无法确认：诊断值 UNKNOWN。

只接受整数 0/1，bool、浮点、字符串数字、null 均不自动转换。
UNKNOWN 不能插补为 0、改变标签、悄悄剔除后发布完整数据集。
出现 UNKNOWN 时阻止完整合格 v2 数据发布；Hash/来源错误直接 BLOCKED，不伪装成 UNKNOWN。
该字段不表示真实提交状态，不是失败标签，也不建议任何干预动作。

### 现有解析器的复用边界

现有 `review_public` 会把损坏 JSON 的正常封装记为无错误，同时不完整校验 Call id 和尾部行。
这是本轮对调用链的代码审查发现，没有擅自修改旧模块。
不能直接裸调用它就发布 v2；Day 3 应先在独立封装中验证关联、合法 JSON 和规范错误封装，
再复用它的公开错误语义解析，不新建第二套同义语义分类器。

错误正文排除参数回显、系统示例和框架建议。显式响应 Call id 必须匹配 Memory 调用 ID；
无响应 ID 的单调用必须有唯一捕获对应关系；多调用仅凭数量/顺序不足，
缺少显式唯一关联则 UNKNOWN。多行正文、未知后缀或损坏格式同样 UNKNOWN。
规则的确切封装、可接受框架后缀和范围见新协议。

这些是 **已冻结的候选要求**，本日没有实现 Day 3 的消息封装或真实回填入口，
不能将静态合同检查写成对全部真实响应边界的实现验收。

## Files Changed

- `configs/formal_decision_features_v2_candidate.json`：候选定义与关闭的执行授权。
- `docs/phase2/formal_decision_features_v2_protocol.md`：完整协议、边界、后续验收与停止条件。
- `docs/phase2/formal_decision_features_v2_candidate_manifest.json`：绑定 16 个新合同/冻结依赖文件。
- `src/risk_agent/prediction/train_public_signals_v2.py`：无提取/回填/拟合 API 的只读候选合同模块。
- `tests/test_feature_v2_candidate_contract.py`：37 项新增候选合同测试。
- 本日报。

## Artifacts Generated

本轮唯一审计目录：
`runs/phase2_p2_d4_day2/p2_d4_day2_20261128_6d42fdd9/`。
保存初始 Git/文件 Hash/历史元数据、Day 1 前置核验、测试、候选预检脚本和结果、
完整性、交付检查与独立 Artifact Manifest。没有 Sidecar、模型或个体预测。

| 冻结对象 | SHA-256 |
| --- | --- |
| 候选 Manifest（建议审核引用） | 2517ef42fde46f1a55ba32e6278678dfd170bdd0b44047960fb27c2174f4cd26 |
| 候选 JSON 配置 | 44d91c1cc66d2ef7d5267003b73728376db836d20dc5b380efe8d34c2e6ef49f |
| 候选协议 Markdown | 4ff52ec4426bae84317c1130b97d7077353a006d2b5a54d6c590ee690c450fe3 |

Manifest 不包含自身或本日报，避免自引用 Hash；包含合同模块、测试及原解析器/特征/模型
配置等依赖的 Hash。只读加载器要求调用方提供预期 Manifest SHA，任何成员漂移均拒绝。

## Commands and Tests

```bash
# 实际使用本轮独立离线测试监督器执行以下专项与兼容测试
.venv/bin/python -m pytest -q tests/test_feature_v2_candidate_contract.py \
  tests/test_train_public_signals.py

# 实际只读预检，使用新的默认拒绝读取策略；没有真实 Checkpoint 加载
.venv/bin/python -I -S -B \
  runs/phase2_p2_d4_day2/p2_d4_day2_20261128_6d42fdd9/candidate_preflight.py

.venv/bin/python -m compileall -q src/risk_agent/prediction/train_public_signals_v2.py \
  tests/test_feature_v2_candidate_contract.py
git diff --check
```

结果：**56 passed，0 failed，0 skipped**（37 新增 + 19 原解析兼容测试）。
覆盖字段顺序/数量、基线版本、UNKNOWN 策略、整数值域、禁止授权漂移、来源边界、
解析复用、历史计数不作规则、Manifest/成员 Hash、路径逃逸和符号链接。
旧解析器兼容用例全部为合成消息，没有重新提取真实 Train。

候选预检结果：DAY2_CANDIDATE_PROTOCOL_PASS，19 项字段，execution_authorized=false，
blocked_events=[]，Checkpoint 提取=0、Sidecar=0、模型拟合=0、禁用数据读取=0。
预检实际核对候选 Manifest 与全部 16 个依赖，未只检查配置中的字符串声明。
compileall、差异空白检查和敏感信息扫描通过；未重复全仓测试。

## Quantitative Results

| 项目 | 本日实际结果 |
| --- | --- |
| 候选字段总数 / 新增字段 | 19 / 1 |
| 原 v1 字段名称、顺序变化 | 0 |
| 候选依赖 Hash 核验 | 16 / 16 |
| 真实 Checkpoint 回填 | 0 |
| Sidecar / 模型比较 / 新采集 | 0 / 0 / 0 |
| UNKNOWN 自动插补 | 0，合同明确拒绝 |
| Validation/Test/未来/隐藏数据读取 | 0 |
| 生产或候选激活 | 0 |

## Data Integrity Audit

开始时已跟踪文件逐项 SHA-256 与结束核验一致，旧候选和冻结协议均未修改。
其他历史 Run 沿用用户选择的元数据保护，大小/mtime 无变化，不打开其内容计算全量 Hash。
Day 1 验收产物及新候选依赖作为本日授权元数据单独验 Hash，不涉及真实响应提取。
本次没有覆盖历史 Run、历史 Manifest、原配置或模型文件。

## Acceptance Checklist

- [x] Day 1 前置 Gate 与验收元数据有效。
- [x] v1 与 v2 候选完全隔离；原 18 字段保持不变。
- [x] 新字段定义、来源、截止点及关联规则固定。
- [x] UNKNOWN 不补零、不改变标签、阻止完整资格发布。
- [x] 不依赖未来或隐藏信息，不使用身份字段作预测变量。
- [x] 优先复用旧公开解析器，独立声明其缺少的前置边界检查。
- [x] 独立候选配置、协议、Manifest 与 Hash 生成。
- [x] 专项、兼容测试和只读合同预检通过。
- [x] 未激活、未回填、未训练、未采集、未进入 Day 3。

## Known Issues

- Day 3 的边界封装与真实 Sidecar 尚未实现/运行；其专项测试也留待该阶段。
- 原审查器单独使用不足以发布 v2，不能忽略新协议的调用前置条件。
- 4 个历史正值只覆盖 2 个模板，不能据此声称可泛化或能改善模型。
- 当前协议只识别规范英文短语，不做自然语言提交状态推理。
- 历史目标计数是待复核的预期，不能用来修改解析规则迎合样本。

## Remaining Work

提交候选协议供用户明确批准。批准应引用本日报中的候选 Manifest Hash，
并明确仅授权 Day 3 Train 历史回填；Day 4 模型比较继续需要独立批准。
审核前所有执行授权保持 false，本轮不继续进入下一天。

## Status: PASS

仅表示 Day 2 候选协议、配置、隔离与合同校验通过；不是 v2 数据或风险模型验收。

## Git Commit

候选内容冻结 Commit：`8f08923a9281cf03b95ca961f1dee43a3c9fb06a`。
验收后本日报单独追加，按 AGENTS.md 第 15 节推送 origin/main。
实际交付 Commit 和 Push 结果另存最终收据并在回复中说明。
