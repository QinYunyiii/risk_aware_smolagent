# Aggregate-family Validation entry v1

用户批准聚合故障目标提案的 Stage A 实施。新增策略为 aggregate-family-v1，匹配
Data Analysis 的显式 F2/F5 聚合操作组；未启用策略的旧 case 仍精确匹配。
保留两个实际工具名、输入、错误、参数 Hash、Replay Signature 和每工具计数。
共享首次匹配序号从现有计数推导，状态仍随原 Checkpoint v2 保存和恢复。

独立清单 aggregate_fault_target_manifest_v1.json 验证新代码/策略/入口及原 Validation
清单链。只在深拷贝的 resolved_plan 中给适用 case 增加 target_match_policy；原计划不改。
元数据适用范围是 Validation 2、Test 2、Train 0；入口只执行原 Validation 32+7。
新 experiment_id、策略 Hash、修订清单 Hash 和 resolved_plan Hash 在任何请求前写入
不可变身份记录，传播至 Provider、故障、工具、特征、结果及最终资格索引。

Source、CONTINUE 到终止及 Verifier、18 特征、六类 Restore Hash、首次输入、预算保护、
因果顺序、覆盖与重复门禁均复用固定原实现。真实模型仍可不调用任何组成员，这种情况
必须记未触发，不能自动生成资格、补任务或改标签。

默认 local-preflight；offline-gate 使用显式 Mock SDK，完整覆盖 read_csv→aggregate_records
路径，Fixture 自创建时为非正式，永不发布正式资格。两种本地模式禁止真实网络和远程工具。

```bash
.venv/bin/python scripts/run_aggregate_validation.py --mode local-preflight --run-id <new-id>
.venv/bin/python scripts/run_aggregate_validation.py --mode offline-gate --run-id <new-id>
```

未来真实 validation 模式要求独立 --authorize-aggregate-validation、已获资格 Train
证明、绑定当前新修订/策略的完整离线 Gate 收据、新鲜且绑定本 Run 的远端证明，以及
原严格 TLS、鉴权、Worker 所有权、干净已提交代码、资源释放等所有前置检查。
旧 Validation 收据不够；不启动或重启服务，不分配 GPU，不调用 Test。

新 cohort claim 位于独立 phase2_validation_target_control，按原计划 Hash 和固定策略
配置 Hash 定位，内容绑定新代码 Manifest；只换 Run ID 或重建代码 Manifest 不能重放。
旧 phase2_validation_control 的 claim 保留。本轮不创建真实 claim 或真实采集 Run。

旧 32+7、26/6 标签、BLOCKED 和未获资格状态不变，不将两项补跑与旧 30 项拼接发布。
未来若授权重采，执行完整独立修订 cohort 并披露旧 Val 已用于工程设计。Train 95 行
资格不改写；Test 仍封存。真实执行、模型训练和 Test 不属于本次 Stage A 授权。
