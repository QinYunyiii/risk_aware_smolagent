# 正式采集启动方案：待审核候选 v1

本方案落实用户“开始正式采集”的目标，先补齐正式生产条件。它是可审核的完整
排程候选，**尚未冻结、尚不可执行**，没有重新标记或导入历史 Smoke。

完整 160 行候选排程见 configs/formal_collection_candidate_v1.yaml。
源协议为 risk-continue-v2.2.0；父清单 Hash 为
48ddf9fb43fbed19ac5f40bd6d14c6879a267464616b42673b9e34b36679dc11。
原五任务真实 Gate 保持 PASS；正式批量生产条件当前为 BLOCKED。

## 为什么需要先审核这份排程

主协议 protocol_risk_v2.md 第 8 节要求：Train 的 F6 覆盖 committed、not_committed、
unknown；Validation/Test 各至少两个 F6，且包括 unknown。
直接展开模板级故障控制后，Train 只有两个 not_committed，Validation 只有两个
committed，Test 有两个 unknown；前两个 Split 不符合主协议。
另有 80 个旧模板实例没有模板级故障配置，不能静默当成已经预注册的正式 F0。

候选排程补齐所有 160 个实例的显式分配，并在不改任务内容的前提下解决上述冲突。
这是故障分配的新增/调整，须依 AGENTS.md 第 4 节获得确认；不通过更新旧 Manifest
隐藏差异，也不根据已观察到的 CONTINUE 标签调整排程。

## 保持不变

- 原 80 模板、160 实例、Task ID、Prompt、数据/预期结果和确定性 Verifier。
- 模板分组 Split：Train 96、Validation 32、Test 32。
- 首个合格非终止 Observation/Error 捕获一个 primary Checkpoint。
- Qwen3-8B、1024 Completion、Seed/Temperature/thinking、Context 和全部任务预算。
- CONTINUE 完整恢复/执行/Verifier 及原 0/1/null 语义；不做 RETRY/REPLAN 分支。
- 原有所有清单和历史 Run；五条真实 Gate 数据永久不并入正式训练集。

## 候选故障排程

每个 Domain × Split 均预注册 F0、F1、F2、F3 和可见 no-progress 形式的 F5。
F4 仅分配给有版本字段的 API 订单读取；非 API 的 F4 不在没有真实版本契约时
伪造为“已覆盖”。F6 只分配到本地 API 写操作，保留三态提交安全约束。

| Split | 候选 F6 状态覆盖 | 主协议要求 |
| --- | --- | --- |
| Train | 至少包含三种提交状态 | 三种均出现 |
| Validation | committed、unknown | 至少两个，含 unknown |
| Test | 保留两个 unknown | 至少两个，含 unknown |

排程首先保留兼容原控制的配置，再按 Task ID 的固定顺序补齐缺失单元；没有读取
模型结果、Verifier 标签或 Test 结果。实际逐任务选择以候选 YAML 为准，不能在
运行时按成功/失败重抽。F4 的 stale_result 使用公开初始订单 ID/status 和旧版本，
不使用 hidden expected 来构造可见响应。

F5 候选明确标记 adapter_implementation_required=true。当前 FaultConfig/Injector
只支持“第二次及以后调用复用已有结果”的 profile，不能把首次调用计数改成 1
就声称实现 no-progress。正式入口必须新增版本化、可验证的真实 no-progress
响应：操作未执行、无状态进展，Agent 实际收到该响应；不得只改内部计数，
不得在模型轨迹中强插重复动作或隐藏故障 ID。

## 规模、重复、资源与停止条件

按 Train → Validation → Test 执行 160 个 primary Source attempts，逐任务交替
路由到两个经重新核验的现有 Worker，Source/CONTINUE 使用同一个 Worker。
不启动或重启 vLLM、不分配新 GPU、不改远端服务；真实运行前重新预检。

0.20 重复比例具体预注册为 32 个 Checkpoint 重复目标：19/7/6 分配到三 Split，
按 sha256(formal-repeat-v1|task_id) 排序选定。它们是同一 Checkpoint 的隔离重复
CONTINUE，不是增加 primary Task 或新 Checkpoint。入选任务无合格 Checkpoint 时
记录未发生，不按未来结果替换。分析按 Task/Template 聚类，不能当作独立行扩样。

160 个 primary 的最大 Token 预算为 8M；32 个重复按原剩余预算执行，另行记成本，
保守追加上限不超过 1.6M。上限不是预计用量；实际共享服务时长以逐项监测为准。
本地预留磁盘、输出目录、Worker 身份和安全释放均须预检。

每任务最多一次 Source Attempt，自动重试/Failover/替换均为 0。已完成任务不能
重复提交；中断尝试进入 append-only journal，不因重启程序自动重放。发生写入、
unknown、Hash/首次输入/跨分支/特征溯源异常时停止并保留证据。
正常无合格捕获或有效失败如实记录，不能补任务、改捕获点或重跑到成功来凑数。

## 正式生产者和特征门禁

不能把五任务 Smoke 入口的 formal_experiment 改成 true 后直接复用。
新正式生产者须从记录创建时使用正式 experiment/task/branch 身份和完整 lineage；
隔离历史 Smoke、Source、分支、内部故障审计、决策特征和 Test 标签。

原 21 个候选特征尚未启用。候选先实现其中 18 个已有定义且可证明来源的特征；
no_progress_count、progress_delta、state_change_count 继续禁用，直到有独立冻结的
领域进展/可见状态契约。完整候选特征名见 YAML，不能复用旧 26 特征提取器里的
fault_*、last_failure 或隐藏提交真值。

必须在 CONTINUE 开始前，从冻结 Agent Memory、已完成可见工具事件、Source
Usage/Latency 和预算生成特征及 provenance/hash，再执行分支并按 lineage join。
故障分配、future outcome、expected、Verifier、未来成本均不得进入特征。
Test 标签封存，不在采集调试中据 Test 结果改提示、分布、特征或阈值。

正式质量要求沿用：目标有效 Checkpoint 96/32/32、最低 72/24/24（总 120），
Restore/首次输入 100%，关键 Usage 可观测率至少 95%，标签 0/1/null 如实保留。
类不足须报告；不修改标签、不自扩任务。LightGBM 的最低 200 Train 有效行及
每类 30 等原门禁不因本次 96 个 Train 任务而降低。

## 审核后的执行顺序

1. 按批准的候选排程发布独立正式计划及清单，保留旧模板控制和所有旧 Manifest。
2. 实现正式批量入口、故障状态恢复、18 特征的 cutoff/provenance 和只追加续跑日志。
3. 本地 Fake SDK 完整故障/恢复/封存/幂等续跑验收及相关回归通过。
4. 重新核验 SSH、TLS、现有 Worker/模型/GPU；先开始 Train 96，再按冻结阶段推进。

本文件和候选 YAML 的存在不授权绕过上述条件，也不意味着已有正式数据。
