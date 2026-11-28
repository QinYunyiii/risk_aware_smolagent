# Decision Order / Train Continuation — Stage A — 2026-11-28

## Objectives

解决 CLOCK_REALTIME 回退对特征截止点审计的影响，保留原 UTC、研究协议及全部历史证据。
接通显式 parent/child 入口，只执行原批准排程中未尝试的 17 个 Train 任务和 6 个预选重复。

## Findings and Change

只读复核 40 秒、3807 次采样，再次观测 1 次 UTC 回退；未调整系统时间或同步服务。
预算本来使用 CLOCK_MONOTONIC，不受此次 UTC 倒退影响。原 cutoff 测试只用 UTC 排序，
不足以在实时时钟可回调的环境中证明执行顺序。

新增旁路决策顺序日志：连续序号、同进程 epoch、monotonic_ns 和原有 RecordLog Hash 链。
18 特征按原实现生成并 fsync 后，追加绑定特征文件 Hash/值 Hash/Checkpoint 的 cutoff。
SDK 委托前必须存在同进程 cutoff、特征文件未变，且请求边界记录已持久化。Source 位于
cutoff 前，CONTINUE/重复在其后；所有 Provider Audit ID 必须与顺序证据逐项配对。
特征序号/单调时间只用于审计，不进入模型输入、风险特征或 Checkpoint Schema。

门禁失败会锁存，禁止同任务自动恢复请求；缺失、改写、跨进程复用 cutoff、单调时钟回退
或日志写入失败均 fail-closed。专项还核验了这类本地审计故障的 canonical outcome 是 null，
不能只在外层排除样本、却在内层误记 Agent 失败。每任务归档将原始顺序证据纳入原 journal Hash。

独立 `decision_order_manifest_v1.json` 固定新增 5 个实现/说明文件，SHA-256：
`bbd4bc3bb302590f74bd1626600efed0eedc218e16ed0ee5f691f7eb9bbffefd`。
它绑定 budget-boundary-audit-v1 清单及所有父清单；旧生产代码、Manifest 和历史结果均不修改。
研究问题、任务、Split、Prompt、Verifier、故障排程、18 特征定义、捕获规则及预算均不变。
新增审计是原“特征先落盘、再 CONTINUE”规则的执行证明，不是放宽该规则。

## Continuation Scope

父 Run：`formal_train_20261127_ad34d6d2`，可信原始 Manifest：
`8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00`。
父 Run 的 79 次尝试、78 个有效主样本、13 个有效重复、1 个空标签及原 BLOCKED 不改写。

子入口 `run_formal_train_continuation.py` 重新验证父 Manifest/记录链/任务前缀，只能运行
确定的剩余 17+6；全部已尝试任务在模型调用前拒绝。任务顺序、Worker、Fault 参数和重复
选择沿用原计划。显式 continuation_binding 记录子范围；继承的 run_manifest 的 96/19
仍描述原整体排程。真实入口通过不可覆盖的 cohort launch claim 防止换新 Run ID 偷偷重复。

合并审核再次验证父/子不可变证据、96 个唯一 Source attempts、原预选重复、有效样本恢复
与首次输入、子因果证据、原故障覆盖和两类标签。无合格 Checkpoint 的重复机会如实记录
未发生，不补任务；重复不能扩成独立训练行。只有整批合格且连接资源释放后，才允许发布
Train-only 资格引用索引，绝不改写原始行、旧空标签或旧 Gate。整个正式数据集仍不就绪。

## Tests

- 首轮新增专项 10 passed；补齐保护边界后专项（排除较长 child cohort 项）11 passed，1 deselected。
- 最终全仓：**1114 passed，0 failed，0 skipped**，231.22 秒。
- 新专项故意令 Provider UTC 每次倒退，仍得到正确预算终止与完整因果证据；缺失/改写/不可写
  cutoff 在首次 CONTINUE SDK 前拒绝，Source 请求仍只有 1 次，canonical outcome 为 null。
- 还覆盖跨 epoch 拒绝、单调时钟回退、重复 Audit ID、Source 越过 cutoff、无边界收据拒绝、
  parent/child 精确任务分配、旧 null 保留、禁止重复 child invocation、未授权真实入口拒绝。
- 旧 `test_formal_train_collection.py` 仅给非正式 Fixture 注入稳定的模拟 UTC，原先的时间
  排序断言一字未减；真实 UTC 回退由新增故障注入用例单独验证，生产 UTC 原值不改。
- 独立 CLI 离线演练：从真实只读父 Run 推导 17+6，子任务全部完成；988 个产物文件 Hash
  和长度匹配。混合父证据与模拟子数据的合并结果明确为非正式，不发布训练数据资格。
- compileall、diff check、父清单链校验通过。

实际主要命令：

```bash
.venv/bin/python runs/phase2_causal_order/causal_order_20261128_c4110bb9/offline_test_guard.py tests/test_decision_order.py -q
.venv/bin/python runs/phase2_causal_order/causal_order_20261128_c4110bb9/offline_test_guard.py -q
.venv/bin/python scripts/run_formal_train_continuation.py --mode offline-gate --parent-run runs/phase2_formal_train/formal_train_20261127_ad34d6d2 --parent-manifest-sha256 8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00 --run-id causal_child_fixture_20261128_c4110bb9 --output-root runs/phase2_causal_order/causal_order_20261128_c4110bb9
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## Evidence, Git and Resources

本地原始审计：`runs/phase2_causal_order/causal_order_20261128_c4110bb9/`，包含基线、时钟
采样、专项/全仓日志、离线子 Run、计划/合并核验及绑定当前清单的本地资格收据。
本任务开始时 HEAD `8806f92`、工作区干净；31144 个历史文件已建立 SHA-256 基线。
旧生产/配置/Manifest 不变；原跟踪测试文件的唯一修改是上述 Fixture 时钟控制。

本地资格阶段实验室 SSH、HTTP/Qwen、GPU、vLLM、Docker 操作为 0；没有改系统时间、
没有安装依赖或重启任何服务。Git 只提交实现、测试与脱敏文档，不提交原始运行数据或凭据。

## Next Execution and Status

依用户对正式 96/19 排程及修复后“继续”的授权，离线验收、提交并推送后，重新核验现有
两 Worker 的身份/模型/CA/GPU 映射，使用独立 child Run 执行且仅执行剩余 17+6。
任一基础设施/安全/证据门禁失败立即停止，零自动重试、零 Failover，不启动或重启 vLLM。
本报告仅为实施及本地资格 PASS，不宣称真实 child 已完成；真实结果另发日报。

## Status: PASS
