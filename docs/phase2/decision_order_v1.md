# Decision Order v1

UTC 原值继续保存，可能回退，不作为跨阶段因果顺序的唯一证明。预算定义和原有
18 个特征值/来源、Checkpoint v2、Prompt、任务、Split、Verifier、故障排程均不变。
新增决策顺序旁路审计是既有“特征先落盘、再 CONTINUE”规则的执行证明。

每个 Run 保存 decision_order.jsonl：连续序号、同进程 epoch、monotonic_ns 和 Hash 链。
特征包落盘并记录原 journal 后，追加绑定 Checkpoint、特征值 Hash、特征文件 Hash 的
feature_cutoff，fsync 返回后才允许 CONTINUE/重复。每次实际 SDK 委托前追加
provider_request_boundary，绑定 Audit ID 和 cutoff Hash/序号，核验特征文件未变化。
Source 必须在 cutoff 前，CONTINUE/重复必须在同进程 epoch 的 cutoff 后；跨 epoch
沿用旧 cutoff、特征改写、缺失/损坏/无法持久化的证据均在 SDK 前拒绝或使标签为 null。
不能只靠事后时间戳或成功结果补造顺序。单调时钟回退也 fail-closed。

审计完整性包含 Provider/Parsing 配对和决策顺序验证。任务归档保存该任务的原始
顺序日志子集，原 journal 将其纳入不可变 Hash。UTC 回退不修改、不隐藏；新日志
只用于审计，不加入模型输入、风险特征或 Checkpoint Schema。

CausalFormalCollector 使用已冻结 budget-boundary-audit-v1 Agent，因而预算收据修复
继续有效。独立 decision_order_manifest_v1.json 绑定全部父清单，原清单不重写。

TrainContinuationCollector 只允许父 Run 从未尝试的精确队列。保留原父 BLOCKED 和
空标签，child 采用独立 Run ID 与显式 continuation_binding，禁止旧 --resume、
重复 Source、替换任务或 Worker Failover。继承的 run_manifest 中 96/19 是原整体
排程，child 的执行范围以不可变 continuation_binding 和 child summary 为准。
真实入口持久化独占 cohort launch claim，换一个 Run ID 也不能自动重复剩余队列。

后续合并审核逐项核验父/子不可变证据、96 个唯一 Source attempts、19 个预选重复、
有效样本的六类 Hash/首次输入/特征顺序及原故障覆盖。旧空标签保留，最多 95 个有效
主样本。只有完整性、覆盖、数量和两类标签门禁满足，并且资源已释放，才发布
Train-only 资格索引；原始行与旧状态不回写，重复不作为额外独立样本，整体数据集
仍不就绪，不运行 Validation/Test 或训练。

本地旧 Fixture 用显式稳定的模拟 UTC，原时间断言不删减；新专项故意注入 UTC
倒退，证明前置门禁基于单调/因果顺序而非“碰巧本次时钟正常”。不调整系统时钟。
