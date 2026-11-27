# 正式采集启动预检与候选排程

日期：2026-11-27（Asia/Shanghai）。基线：
253c05eba2fc72611c64061beba139d60863ff01。初始工作区干净。
本轮目标：落实用户“开始正式采集”的指令，先核实正式生产入口和冻结条件。

## Status: BLOCKED

**正式采集尚未启动，新增正式样本为 0。** 阻塞发生在正式生产者与实验排程，
不是新的 SSH/TLS 故障。之前的真实五任务工程 Gate 仍为 PASS，其原 260 个
Artifact 文件重新验证通过；没有降级、重新标注或导入这些 Smoke 数据。

当前正式就绪检查为 FORMAL_COLLECTION_BLOCKED。本轮没有实验 SSH、隧道、HTTP、
模型请求、GPU 查询/修改、vLLM/Docker 操作或远端文件写入。

## Objectives / Local Environment Work

已读取仓库规范、项目指南、最近日报、v2.2 配置和验收产物，并审计实际调用链，
没有只凭“配置写了 formal”判断正式能力已经存在。
本轮开始建立历史文件和 Git 跟踪文件 Hash 基线。所有私有输出写入新的独立目录：
runs/phase2_formal_readiness/formal_readiness_20261127_6fcccb82/。

## Work Completed / 四个已证实的阻塞项

1. **已验收的入口仍是五任务 Smoke。** continue_acceptance_v2.load_entry 明确
   限制五个不同 Train Task；记录标记来自原 FLAGS，formal_experiment=false。
   不存在从该入口切到正式 160 任务、完整逐记录 formal lineage、Test 封存和
   幂等续跑的模式。run_day8_collection 是历史轨迹入口，不是完整 v2 正式生产者。
2. **没有正式实例级故障排程。** 80 个旧模板实例没有模板级 fault_plan；新增
   模板的控制用于参考探针，不能自动等同于正式 Source/CONTINUE 故障安排。
3. **模板级 F6 展开后违反主协议分布。** 具体对比如下。
4. **v2 特征提取与启用仍待实现。** 原 feature_allowlist 的 21 个候选均为 disabled，
   computation_code_location 仍为 PENDING；不能用旧 prediction.dataset 的 fault_*
   等隐藏元数据特征替代，更不能事后从 outcome 补成决策时刻特征。

| Split | 当前模板控制展开为实例 | 主协议第 8 节 | 结果 |
| --- | --- | --- | --- |
| Train | 2 × not_committed | 三种 F6 提交状态均覆盖 | 不满足 |
| Validation | 2 × committed | 至少两例且含 unknown | 不满足 |
| Test | 2 × unknown | 至少两例且含 unknown | 此项满足 |

这些是采集前发现的设计/生产门禁缺口，尚未消耗真实模型预算或产生受影响的正式数据。

## 新增产物与可审核方案

- src/risk_agent/collection/formal_readiness.py：纯本地审计，校验冻结父链和真实
  Gate 文件集/Hash，展开全部 160 个任务的模板控制及 Domain/Split/F6 覆盖，
  检查特征启用状态。它只审计既有入口，不能通过切换布尔值授予正式就绪。
- scripts/audit_formal_collection_readiness.py：独占目录、长度/SHA-256 清单和
  明确 BLOCKED 退出码 2；不创建模型客户端、不读取凭据。
- configs/formal_collection_candidate_v1.yaml：**160 行完整候选排程**、固定路由、
  32 个预选重复目标、18 个候选特征和执行前置条件。
- docs/phase2/formal_collection_candidate_v1.md：具体差异、预算、阶段与安全规则。
- tests/test_formal_collection_readiness.py：就绪审计、伪造/损坏证明拒绝、独占写入、
  无凭据访问及候选排程验证。

候选状态为 PROPOSED_NOT_FROZEN_NOT_EXECUTABLE，不会被现有入口自动加载执行。
它保留原 Task/Prompt/expected/Split/Verifier/模型参数及全部任务预算，按主协议：

- 为 160 个实例显式分配故障；所有 12 个 Domain × Split 单元都包含
  F0/F1/F2/F3/F5。
- F4 限于具有公开版本字段的 API；没有为其他领域伪造版本契约。F6 仅用于 API 写。
- Train 补齐三态 F6；Validation 含 committed/unknown；Test 保留 unknown。
- F5 使用主协议允许的可见 no-progress，明确标注新适配器尚未实现。原 F5
  schema 只允许第 2 次及以后调用，测试验证候选不能冒充已有能力直接执行。
- 先 Train 96、再 Validation 32、Test 32；Test 标签封存。每任务一次 Source
  Attempt，零自动重试、Failover 和替换，写入/unknown 后不得盲目重放。
- 0.20 的重复比例具体选定 32 个 Checkpoint 目标，19/7/6，按公开的 Task ID
  Hash 排序，不读取标签，捕获缺失时不另挑成功案例替代。
- 18 个已有定义的候选特征只在截止时刻及溯源测试通过后启用；需要领域进展契约的
  no_progress_count/progress_delta/state_change_count 继续禁用。

完整规则和逐任务分配可直接审阅 YAML，不需要先启动模型。候选生成只使用冻结
任务的标识、Split、领域、工具名称和公开初始状态，不读取 CONTINUE/Verifier 结果。
生成脚本和覆盖汇总保存在本次私有审计目录，原冻结模板和清单保持不变。

## Commands and Tests

```bash
.venv/bin/python scripts/audit_formal_collection_readiness.py \
  --gate-run runs/phase2_continue_acceptance_v2/p2_continue_v22_real_20261127_0243a9d0 \
  --output-dir runs/phase2_formal_readiness/formal_readiness_20261127_6fcccb82/inventory
.venv/bin/python runs/phase2_formal_readiness/formal_readiness_20261127_6fcccb82/offline_test_guard.py \
  tests/test_formal_collection_readiness.py tests/test_protocol_freeze.py \
  tests/test_continue_output_contract_v2.py::test_only_approved_profile_and_remote_fields_change -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

审计命令按设计返回 2，表示正式生产前置条件未满足，不是程序崩溃。
测试 **29 passed，0 failed，0 skipped，9.81 秒**。测试有独立网络守卫；urllib3
导入时的本地 IPv6 检测 bind 被阻断，没有真实外部连接。
候选的 160 Task ID 全量唯一且与原 Catalog 一致，Split 不变，32 个重复目标的
Hash 选择可复核，12 个 Domain/Split 覆盖单元和 F6 三态约束均通过静态测试。
这些测试不证明尚未实现的正式生产者或 no-progress 适配器已通过真实验收。

## Remote Environment Work / Resource Use

仅本地 WSL 文件、静态配置和历史产物分析；未进行真实连接或计算任务。
没有申请新凭据、创建远端目录、占用新 GPU、修改/重启 Worker 或执行模型请求。
历史文件、冻结文件及原真实 Run 保留；Smoke 正式资格始终为 false。

## Remaining Work / Approval Boundary

需要确认具体候选排程后，实施版本化正式生产入口、故障状态恢复、18 特征的
决策时刻溯源、只追加续跑和 Test 封存，完成离线集成回归，再重新核验现有服务
并从 Train 96 个任务开始。不能直接把已验收的 Smoke 标记改成 formal。

已向用户展示候选并请求一次确认。依据 AGENTS.md 第 4 节，冻结的故障分配涉及
研究设计变更，需先说明原因并确认。本轮没有把原“开始正式采集”的授权扩大
解释为可以静默改动已有故障分配。原正式采集授权在后续实施中继续有效，不应
在同一方案获批后重复请求。

本轮仅新增离线检查、不可执行候选、测试和文档。依仓库规范，测试/编译/敏感
信息及差异检查通过后自动 Commit / Push；原始审计和历史数据不上传。
GitHub 交付网络另计，不属于服务器实验连接。
