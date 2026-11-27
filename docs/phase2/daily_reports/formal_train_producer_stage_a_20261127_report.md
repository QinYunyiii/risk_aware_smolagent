# Formal Train Producer — Stage A Report

## Objectives

实施用户于 2026-11-27 批准的 `formal_collection_candidate_v1.yaml`，本地验收后执行
Train 96 个一次性 Source attempts，以及预选的 19 个同 Checkpoint 重复 CONTINUE。
本报告只记录正式运行前的本地工程验收，不宣称已取得真实 Train 数据。

## Work Completed

- 发布独立正式计划 `configs/formal_collection_v1.yaml`，原候选不变。
  F6 提交子类型及 F5 首次可见 no-progress 采用获批分配；任务、Split、公开 Prompt、
  Verifier、1024 completion tokens、Seed、Temperature、Checkpoint v2 和预算不变。
- 新增正式 Source/CONTINUE 生产者，真实记录从创建时采用正式身份，禁止导入旧 Smoke。
  CONTINUE 使用 `resume(stream=True)` 到终止，再运行确定性 Verifier；恢复算法与父版本
  的方法 AST 一致，仅替换记录上下文及正式工具工厂。
- F5 在首次匹配只读调用返回可见无进展，不执行操作；Fault 状态随 Checkpoint 恢复。
  F3 的同一被拒参数继续无效；F6 三种内部提交真值不会出现在公开错误文本中。
- 新增 18 特征的独立 Allowlist 与实现，限定决策时已完成信息；特征/溯源 Hash 在首次
  CONTINUE 前持久化。三个领域进展特征继续禁用，不读取内部故障、expected 或未来标签。
- 追加式 Hash 链、每任务文件归档、独占锁及一次性写入，拒绝自动重放未决 Source；
  保留正常无 Checkpoint 情况，服务/Usage/Hash/Replay/资源异常停批并保留证据。
- 重复分支独立恢复同一 Checkpoint，继承 Source 剩余预算，不继承主分支消耗；重复行
  不作为额外独立风险样本。真实候选样本需完整批次质量审核后另发资格索引，不回写原始记录。

## Frozen Inputs

- Parent v2.2 manifest: `48ddf9fb43fbed19ac5f40bd6d14c6879a267464616b42673b9e34b36679dc11`。
- 新正式清单: `553ce3c15b34588889775f7c95cadd5ece94219e1f45b18b165e4858a1c82d08`，固定 14 个正式输入文件。
- 原候选: `22ca527b33cb85a8f50611b0dc40cc713cc4e6eea04dae5cc9d746cab27342b7`。
- 历史真实 v2.2 五任务 Gate 的原始 260 文件证明复核通过，不将该 Smoke 导入正式数据。

## Commands and Tests

在本地 WSL `.venv` 执行；测试审计钩子禁止真实 connect/DNS/sendto/bind 及远程工具。

```bash
.venv/bin/python runs/phase2_formal_implementation/formal_stage_a_20261127_9fc537e4/offline_test_guard.py tests/test_formal_train_collection.py -q
.venv/bin/python runs/phase2_formal_implementation/formal_stage_a_20261127_9fc537e4/offline_test_guard.py -q
.venv/bin/python scripts/run_formal_train_collection.py --mode offline-gate --run-id formal_offline_acceptance_20261127_9fc537e4 --output-root runs/phase2_formal_implementation/formal_stage_a_20261127_9fc537e4
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

- 新专项：15 passed；覆盖完整 96+19、六类 Hash、精确输入、特征截止点与污染测试、
  缺失 Usage、不捕获、不重放、归档漂移、记录损坏、文件不可覆盖及未经资格验证的真实入口拒绝。
- 全仓：**1088 passed，0 failed，0 skipped**，142.61 秒。
- 最终离线生产入口：96 个有效主样本，19 个有效独立重复，两类模拟标签及计划故障覆盖满足；
  5234 个产物 SHA-256 全部匹配，未发布正式资格索引，所有 Fixture 自创建时均为非正式。
- 测试期间唯一被审计钩子拦截的 socket 操作是 urllib3 导入时的本地 IPv6 能力检测 bind；
  实际网络请求为 0。compileall 与 diff check 通过。
- 本任务开始时的 11327 个历史文件、362 个既有跟踪文件逐一 SHA-256 匹配，旧清单未变。
- 新文件敏感扫描未发现继承的真实 API Key、私钥或超过 1 MiB 的文件。

## Artifacts Generated

仅本地、不上传原始运行数据：
`runs/phase2_formal_implementation/formal_stage_a_20261127_9fc537e4/`。
包含基线、测试日志、非正式模拟 Run、完整性核验及绑定当前正式清单的
`local_acceptance_receipt.json`。

## Remote Environment Work

本地验收阶段未连接实验室服务器、未请求模型、未创建隧道、未操作 GPU 或 vLLM。
已确认当前环境继承项目 API Key 与 CA，仅记录存在性和 CA Hash，不记录凭据值。

## Remaining Work

按既有授权重新核验现有两个 Worker 的身份、模型、TLS/鉴权、GPU 映射，然后执行
Train 96+19；任一基础设施、安全或证据完整性失败立即停批，不自动重试、替换或 Failover。
Validation/Test 实际采集及模型训练不在本轮范围。即使 Train 全部完成，也不代表
完整正式数据集已经完成或满足后续 LightGBM 训练数量门禁。

## Git Status

本任务开始时工作区干净，HEAD `a34272e6397d87b5fe08dc966bf0d0d5fded8990`。
交付仅新增正式生产者、配置、测试及脱敏文档；原始 Run、凭据、证书和权重不提交。
本轮本地验收通过后按仓库授权提交并推送；实际 Commit 见 Git 历史。

## Status: PASS

仅表示正式生产者及本地资格验收通过，不表示真实 Train 运行通过。
