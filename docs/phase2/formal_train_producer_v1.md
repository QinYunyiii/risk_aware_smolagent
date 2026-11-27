# 正式 Train 生产者 v1

用户已明确批准 formal_collection_candidate_v1.yaml 的故障分配、F5 可见 no-progress、
正式生产者及特征溯源实现，并授权本地验收后开始 Train 96 及 19 个预选重复。
Validation/Test 仍未授权运行，训练模型不在本轮范围。

活动计划为 configs/formal_collection_v1.yaml；原候选、所有旧清单、Gate 和历史 Run
原样保留。任务/Prompt/Catalog/Split/Verifier/模型推理参数和预算来自不变的 v2.2 父协议。
新清单 formal_collection_manifest_v1.json 固定本生产者、正式计划和特征实现，绑定
v2.2 父清单；不通过改旧 Hash 绕过漂移检查。

正式 Source 使用与 v2.2 相同的捕获时刻和 Agent，正式工具适配器仅规范化故障的
公开响应。F5 首次匹配只读调用不执行操作，返回可见无进展错误；计数/事件状态随
Checkpoint 恢复，禁止重复注入或仅设置一个内部重复计数。F6 三种提交真值不进入
公开错误文本，故障状态和事件单列 internal 审计。F3 相同被拒参数不会自动变为合法。

FormalContinueRunner 的恢复/stream/Verifier 方法由固定旧方法复制，仅替换记录
上下文和工具工厂，六类 Hash、首次输入、预算、Replay 和标签规则保持同一算法。
所有新正式记录在创建时采用正式身份；离线 Fixture 从创建时标记非正式，不发布
正式样本。旧 Smoke 永不导入。需要更改公共算法时必须另立版本，不能修补历史 Run。

18 个特征只读取冻结 Agent Memory、已完成 Source 模型/工具计时和预算。特征包及
provenance/hash 在任何 CONTINUE 请求前落盘，随后仅按 lineage join 标签。
内部故障、expected、Verifier、未来事件和重复分支都不作为特征输入。
缺失成本保持 null 并附 provenance，不填零；三个依赖领域进展契约的特征保持禁用。

每任务先写 task_claimed，再执行一次 Source。任务结束后写不可变归档及 task_completed
Hash 链；恢复时验证已完成文件并跳过，存在未决 claim、损坏文件或先前安全/基础设施
停止时拒绝自动重放。以文件锁拒绝同 Run 并发运行。没有合格 Checkpoint 的正常 Source
如实记为未捕获；服务失败保持 null 并停批。只有计划中的同 Checkpoint 重复可另开
隔离分支，继承原 Source 剩余预算，不产生额外独立 primary 行。

本地使用：

```bash
.venv/bin/python scripts/run_formal_train_collection.py --mode local-preflight --run-id <新ID>
.venv/bin/python scripts/run_formal_train_collection.py --mode offline-gate --run-id <新ID>
```

真实模式还要求 --mode train --authorize-formal-train、当前版本的本地验收 receipt、
原真实五任务 Gate 路径、新鲜且绑定本 Run 的远端证据和连接配置。严格 TLS、鉴权、
Worker/模型/GPU 归属和资源释放沿用已验收组件，不重启 Worker，不分配新 GPU。
本地验收和文件完整性不冒充远端身份仍然有效；执行前必须重新核验。

正式有效样本为候选记录，只有全批完整性和预注册质量条件满足才允许数据集发布；
类不足、未捕获、未触发故障、null/中断均如实报告，不能改排程、重抽或改标签。
原始 JSONL 和报告只追加；每次进程会话发布独立 summary/manifest，不覆盖前次。
