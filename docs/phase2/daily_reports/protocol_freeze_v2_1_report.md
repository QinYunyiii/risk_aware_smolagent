# Protocol Freeze v2.1 Report — 2026-11-27

## Objectives

按用户确认的三项决定建立独立正式候选配置：1024 Completion Tokens、
新增多步骤 API 任务完整标识符提示、首个合格非终止 Observation/Error 捕获。
冻结 Checkpoint Schema、有效 Catalog、Split、Failure Taxonomy、Verifier 和预算。

## Local Environment Work

本地 WSL；读取仓库规范、项目指南、README、Day 14 日报及 P2-D2 最新验收记录。
基线 HEAD：`7fac1e5aba961c58913d290391e22988f02f136d`；初始工作区干净。
本轮不运行 Docker、Agent 正式采集或远端实验。

独立本地审计目录：
`runs/phase2_protocol_freeze/p2_protocol_freeze_v2_1_20261127_5341bf1a/`。
该目录被 Git 忽略；原始 baseline、测试日志和本地验证证据留在本机。

## Remote Environment Work

真实实验 SSH、Tunnel、TLS 握手、HTTP/API 请求、Qwen Completion、GPU/vLLM/Docker
操作均为 **0**。未读取 API Key。GitHub 交付连接与实验网络操作分开统计，
按仓库自动 Git 规则执行，不把 Git 上传计作模型或 SSH 实验。

## Work Completed

- 新协议 ID `risk-continue-v2.1.0`；Checkpoint Schema 保持 2.0.0。
- 新配置引用既有独立 1024-token 模型配置，Seed/Temperature/thinking 不变。
  模型调用 10、工具调用 12、总 Tokens 50,000、Context 8,192、请求 120 秒、
  任务 900 秒均保留。Source/CONTINUE 继承已消耗预算。
- 保留两个原始模板文件及默认生成器，原有 40 模板的定义、Prompt、ID、Seed、
  Split 不变。新版本仅修改新增 10 个多步骤 API 模板的 20 个实例 Prompt，
  按每实例公开数据生成完整订单号和幂等键，不读取隐藏 expected/reference/fault 数据。
- 有效 Catalog 80 模板、160 实例；48/16/16 模板与 96/32/32 实例的
  Train/Validation/Test 分配保持不变，完整 ID/Seed/Split 清单已落盘。
- 冻结 96 个本地输入文件、依赖版本及 smolagents Agent Prompt 资源。
  离线加载器逐项检查 Hash，校验有效 TaskSpec 和 Split，漂移即失败。
- 保留首次合格点规则，明确正式生产者须在每个已完成 ActionStep 上尝试捕获；
  本地生命周期验证第一个正常/错误 Observation 的捕获、每任务一个 primary、
  独立 Restore 后保留已耗 Token/工具/时间预算。
- 160/160 派生任务 Reference 执行和 Reset 校验通过，160/160 错误结果被
  原有确定性 Verifier 拒绝。此为离线结构验证，未生成真实 Qwen 标签。

## Files Changed

- `configs/phase2_risk_collection_v2_1.yaml`：独立正式候选配置。
- `configs/task_catalog_v2_1.yaml`：有效 Catalog 的渲染规则与来源。
- `src/risk_agent/collection/protocol_freeze.py`：Prompt 渲染、有效任务加载与完整性检查。
- `scripts/verify_protocol_freeze.py`：只读离线校验入口，无真实运行模式。
- `docs/phase2/protocol_freeze_manifest_v2_1.json`：冻结输入与 canonical Hash。
- `docs/phase2/task_split_manifest_v2_1.json`：160 个实例的固定分组信息。
- `docs/phase2/protocol_freeze_record_v2_1.md`：协议决定、边界与后续验收要求。
- `tests/test_protocol_freeze.py`、`tests/test_model_backed_checkpoint.py`：15 项新增测试。
- `README.md`：新版本入口和就绪状态说明。
- 本日报。

## Artifacts Generated

| 项目 | SHA-256 |
| --- | --- |
| v2.1 有效 Catalog | `96b9deb4d3c89cc3a1084c4fa884ca75b113daef9b29a0c14b688287b7deed2b` |
| 原 P2-D1 Catalog（未变） | `c37f9bb3dee6c8493e8a174aa977595769caf6b7cff8e1601bb60ccdc5b9d64c` |
| 原 200 实例 Catalog（未变） | `8a76a5c2b5d826832f2c604477c606d699a19a8ceee35b923cedc336b9e9d775` |
| canonical Split（未变） | `baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9` |
| v2.1 Freeze Manifest | `32e12bf71994919175e2639c6d3da04b529d56e49007486be42b6b9add22888c` |

本地保留 `baseline.json`、`offline_validation.py`、`offline_acceptance.json`、
`offline_test_guard.py`、按次编号的 `test_results*.json` 和统一 Artifact Manifest。
历史 7,864 个产物文件逐项 SHA-256 与本轮开始前一致。

## Commands and Tests

均在本地 WSL 执行：

```bash
.venv/bin/python scripts/verify_protocol_freeze.py
.venv/bin/python runs/phase2_protocol_freeze/p2_protocol_freeze_v2_1_20261127_5341bf1a/offline_test_guard.py -q tests/test_protocol_freeze.py tests/test_model_backed_checkpoint.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_protocol_freeze/p2_protocol_freeze_v2_1_20261127_5341bf1a/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python runs/phase2_protocol_freeze/p2_protocol_freeze_v2_1_20261127_5341bf1a/offline_validation.py
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Test Results

- 最终专项：**19 passed**（包括原有生命周期用例）。
- 新增：**15 passed**；全仓：**802 passed，0 failed，0 skipped**，75.32 秒。
- 初次专项曾有 1 项测试 Fixture 因空 Reference Steps 被 TaskSpec 构造器拒绝；
  已改为合法但不同的 Reference Steps，最终专项和全仓均通过，保留初次结果。
- Reference/Reset：160/160；错误结果拒绝：160/160。
- `compileall`、diff 空白检查、敏感信息扫描和历史完整性检查通过。
- 离线 Guard 禁止 socket 连接、DNS、发送及真实 SSH/OpenSSL/curl/wget。
  导入依赖期间出现一次被拒绝的 IPv6 支持检测 bind；未建立监听或外部连接。
  成功实验网络操作数为 0。

## Acceptance Checklist

- [x] 新旧配置分离，256-token 历史配置保持原样。
- [x] 提示按所有同类新增实例统一生成，未只为 T3 写死订单号。
- [x] 不改变 Split、Reference、expected、故障计划与 Verifier。
- [x] 具体 Catalog、Split、预算、Schema、Taxonomy、Verifier/Prompt 可校验。
- [x] 首个正常/错误 Observation 可捕获，恢复预算不清零。
- [x] 历史文件不变，全仓离线回归通过。
- [x] 未将历史 Smoke 结果转为正式标签。

## Known Issues

新加载器尚未接入获准的正式 Source/CONTINUE 生产运行入口；它明确只提供离线
完整性检查，不声明完成真实模型采集链路接线。未来入口必须使用该加载器、
在每个完成的 ActionStep 上检查捕获，并记录 Manifest/Catalog/Split Hash。

P2-D2 的成功证据是第二次 Observation 后捕获并发送一次恢复请求；
它不等于本版首个合格点的完整 CONTINUE/Verifier 验收。
本轮本地 Stub 生命周期验证也不能代替真实 Qwen 证据。

## Remaining Work

下一阶段单独批准 Train 范围的新配置完整链路验收：Source 首个合格点捕获、
独立 Restore、六类 Hash/精确输入、`resume(stream=True)` 到终止、Verifier、
Provider Audit、Replay Guard 和持续预算。通过并审核后才能批准正式采集。
不自动进入 P2-D3，不扩展 RETRY/REPLAN。

## Status: PASS

**PROTOCOL_FREEZE_LOCAL_PASS**：本地冻结范围全部通过。
**正式采集就绪：false**，真实完整 CONTINUE/Verifier 验收仍待执行。
该状态不改变任何历史 Run 的 Gate。

## Git Commit

本报告不嵌入自身所属 Commit SHA，避免自引用；最终提交可通过
`git log --oneline -- docs/phase2/daily_reports/protocol_freeze_v2_1_report.md` 查询。
交付遵守 AGENTS.md 第 15 节，只提交本轮文件；原始 runs、凭据和实验日志不进入 Git。
