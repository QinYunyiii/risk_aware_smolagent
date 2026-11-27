# Protocol Freeze v2.1 — 2026-11-27

## 冻结决定与适用范围

协议 ID：`risk-continue-v2.1.0`。本轮经用户确认，将正式采集的单次
Completion 上限设为 1024，并将完整标识符提示推广到全部新增多步骤 API 模板。
Checkpoint Schema 仍为 **2.0.0**，研究问题仍为
`r_t = P(Y_continue = 0 | X_≤t)`，当前只学习 CONTINUE。

这是独立版本。历史 `phase2_risk_collection.yaml`、256-token 模型配置、
两个原始模板文件、T3 Gate 专用配置和历史 Run 均保留。
原协议的标签、F0–F6 定义、评价指标和数据隔离要求继续适用；
本文件只明确批准的预算与提示版本，并把已实现 Catalog/Split 固定为具体内容。

配置入口：`configs/phase2_risk_collection_v2_1.yaml`。
离线加载入口：`risk_agent.collection.protocol_freeze.load_frozen_collection(root)`。
旧 Gate/采集入口不会因为新文件存在就自动切换到该版本。

## 冻结内容

| 项目 | v2.1 决定 | 校验依据 |
| --- | --- | --- |
| Checkpoint | Schema 2.0.0，六类 Hash、独立 Restore、精确首次输入、Replay Guard | 原 Schema/实现 SHA-256 |
| Catalog | 原有 40 + 新增 40 模板，每模板 2 实例，共 160 | 生成后的完整 TaskSpec canonical SHA-256 |
| Prompt | `p2-multi-api-exact-identifiers-v1` | 渲染器、策略文件及完整有效 Catalog Hash |
| Split | 模板分组，48/16/16 模板，96/32/32 实例 | 完整 160 行 ID/Seed/Split 清单及 canonical split Hash |
| Failure Taxonomy | 原 F0–F6、原触发计划与副作用安全规则 | 原文档、模板和故障实现 Hash |
| Verifier / Label | 确定性最终状态验证；成功 1、有效失败 0、基础设施中断等 null | 原 Verifier、标签文档和结果分类实现 Hash |
| 模型 | Qwen3-8B；Seed 20261118；Temperature 0；thinking false | 已验收的独立 1024-token 模型配置 Hash |
| System Prompt / Tool Schema | 保持既有 Agent Prompt；工具仍本地执行 | smolagents 1.26.0 Prompt 资源、Agent/Tool 源码 Hash |

具体 Hash 以 `protocol_freeze_manifest_v2_1.json` 为准。该清单区分
原始文件 Hash、旧 Catalog canonical Hash、新 Catalog canonical Hash 和 Split Hash；
这些值不能互相替代。旧配置中的 `existing_catalog_sha256` 是原有 200 个实例的
canonical Catalog Hash，并非模板 YAML 原始文件 Hash。

清单自身的 Hash 由每次离线检查输出并应写入未来 Run。清单不包含自己的 Hash，
避免循环；冻结记录和源文件进入 Git 后由版本历史追踪。运行时不得自动刷新清单。

## Prompt 与数据划分

保留原有 40 模板的定义、提示和 Split。新增 10 个 `multi_api` 模板的全部
20 个实例采用同一渲染规则：完整订单号、完整幂等键，以及不得缩短或规范化标识符。
订单号取自实例公开的初始订单；目标状态取自原任务参数；幂等键按模板与实例生成。
没有硬编码 T3 的订单号，不读取 expected、Reference Steps 或隐藏故障信息，
不自动纠正模型返回的工具参数。

该规则由 Train 上已发现的问题决定后统一固化；本轮对 Validation/Test 仅做静态
生成、可解性和 Verifier 正负验证，没有模型执行、阈值调优或标签采集。
同一模板的实例与同一 Checkpoint 的所有分支始终在同一 Split。

## 捕获规则

沿用 **首个合格的非终止 Tool Observation/Error 决策点**，每任务一个 primary
Checkpoint。正式生产者必须把每个已完成的 ActionStep 交给既有
`DecisionCheckpointCapture` 判定，不能像 P2-D2 Gate 那样主动等到第二次 Observation。

捕获时 Observation/Error 已进入 Agent Memory、同一批工具调用全部结束、下一次
模型请求未开始、Verifier 未执行、仍有模型步数与预算。可见工具错误允许参与采样，
不能只采错误，也不能排除所有错误。终止事件不能捕获；无合格点如实记为未捕获，
不能用未来结果倒选。捕获后 Source 停在 `checkpoint_captured`。

当前加载器只验证配置与生成 Catalog，不冒充已接通的正式 Source Runner。
新增本地生命周期用例验证第一个正常/错误 Observation 的捕获及预算 Restore；
正式 Runner 对每个事件调用捕获器的真实验收仍须在下一阶段完成。

## 预算及 CONTINUE 语义

| 限制 | 数值 |
| --- | --- |
| 单次 Completion 上限 | **1024 tokens**，Source/CONTINUE 一致 |
| 最大 Agent steps / model calls | 10 / 10 |
| 最大 tool calls | 12 |
| 总 tokens / context tokens | 50,000 / 8,192 |
| 单次模型请求超时 | 120 秒 |
| 任务 wall-clock 预算 | 900 秒 |

1024 是每次输出上限，**不是总预算或输入 Context 预算**。总 Token 计数包含
实际 Provider 报告的输入与输出；不可观测值保持缺失，不能填零。
Source 已消耗的模型/工具调用、Token 与时间随 Checkpoint 恢复，CONTINUE 只用
剩余预算；不得在 Source 与 Restore 两侧各重新分配一份完整预算。
既有副作用前最多一次安全基础设施重新尝试规则保留，不授权当前执行任何重试。

正式 CONTINUE 必须独立 Restore，校验六类 Hash、首次输入和 Replay Guard，然后
真正执行 `resume(stream=True)` 到终止，再运行确定性 Verifier。不能以单次
`request_next_model_response()` 代替结果标签。预算用尽且有有效失败验证可为 0；
未得到有效结果的服务中断、环境中断、手动终止为 null，不能自动记为 Agent 失败。

## 完整性检查与变更控制

本地 WSL 执行：

```bash
.venv/bin/python scripts/verify_protocol_freeze.py
```

该入口不读取 API Key，不连接 SSH/API，不运行 Agent。它校验全部冻结文件、
依赖版本、smolagents Prompt 资源、有效 Catalog 和 Split；有漂移即失败。
没有覆盖现有生产连接模块，也没有改变 TLS 或 Worker 配置。
后续正式入口必须显式使用此加载器，并记录 manifest、配置和 Catalog/Split Hash。

修改被冻结研究内容须先说明原因、取得批准、建立新版本，不能更新旧 Manifest
掩盖差异。远端 Worker/GPU 的实时所有权与模型权重身份仍须每次获准实验前核验；
本地源码 Hash 不能证明远端状态未变。

## 当前 Gate 与下一步

本轮目标为 `PROTOCOL_FREEZE_LOCAL_PASS`，只证明本地协议、生成 Catalog 和完整性
检查成立。正式采集就绪状态仍为 **false**。

已有 P2-D2 五任务 Gate 证明第二次 Observation 后的捕获、独立恢复及一次恢复
请求成功；它没有证明本版“首个合格点 + 完整 CONTINUE + Verifier”的全部链路。
历史 Smoke 数据不能转为本版训练数据，也不能事后换协议重标。

下一阶段须单独授权、使用新 Run ID，先做 Train 范围的完整 CONTINUE/Verifier
验收，核对实际 1024 参数、首个合格点、剩余预算、Provider Audit 与结果分类。
通过后再审核正式采集。当前不启动 P2-D3，不提前扩展 RETRY/REPLAN。
