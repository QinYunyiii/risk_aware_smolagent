# Formal Validation Entry v1

此独立入口准备冻结排程中 32 个 Validation 任务与 7 个预选重复，最低有效数 24。
任务、Split、Prompt、Verifier、Fault/Worker 分配、1024 Completion、Seed、Temperature、
Context、全部预算和首个合格 Observation/Error 捕获规则保持原样。Test 仍封存。

旧 Train/续跑入口和所有父 Manifest/历史 Run 不变。新入口只允许 Validation Task ID；
Train/Test 在 claim、模型工厂或工具执行前拒绝；恢复另行校验 Split、当前 Run、执行模式和
研究身份。模型/Worker/Prompt/Verifier/Catalog/Split Hash 原验证仍保留。

Source capture 与完整 CONTINUE/Verifier 方法直接继承固定父实现，不重写。
validation_lifecycle.py 从固定 FormalCollector 的记录方法机械派生，仅替换 Split、
Validation runner 和新清单检查；特征落盘后沿用 decision-order-v1 的前置顺序门禁。
预算边界 not_attempted 收据修复、六类 Hash、首次输入、只读刷新及写入重放保护均复用。
派生方法的 AST 与明确替换后的父方法在测试中逐一比较，防止静默执行语义漂移。

18 特征在首次 CONTINUE 前从原 Source 信息生成，不读 hidden fault/expected/未来结果。
重复独立恢复同一 Checkpoint、继承 Source 剩余预算，不作为额外独立行。没有合格
Checkpoint 的任务/重复保持未发生，不补任务。服务/审计/Usage/Hash/安全异常停批，
0/1/null 不重编码，禁止自动重试/替换/Failover 或换新 Run 重复提交。

Validation 标签只用于模型选择、阈值选择和校准评估，禁止用于拟合 Train 模型权重。
单类数据保留，AUROC/AUPRC 按原协议记 undefined，不调整分配或标签凑两类。
只有真实质量合格且资源已释放后才发布 validation_eligibility_release.json，明确
allowed_for_training=false；离线 Fixture 永远不发布正式资格。

默认 --mode local-preflight，仅本地；--mode offline-gate 使用显式 Fake SDK 完成 32+7。
两者都有禁止真实网络和远程工具的审计钩子。Mock 数据自创建时为非正式，不能改标签转正式。
真实 --mode validation 另需 --authorize-validation、可信已完成 Train 资格证明、绑定当前
版本的完整离线 Gate 收据，以及新鲜的 SSH/Worker/GPU/模型/TLS 证明和连接配置。
Train 证明只验证 Train 结果和 95 行资格引用，不读取 Validation/Test 历史结果或将 Train
数据导入 Validation。实际 SDK 仍只访问自行部署的现有 Qwen 服务，严格 CA 验证，零 SDK 重试。

真实入口预留不可覆盖的 Validation cohort launch claim 防止跨 Run 重放；只清理本次
拥有的客户端与隧道，绝不启动/重启 vLLM、修改 GPU、修改系统时间或远端服务。
本轮 Stage A 仅实施/离线验收，不自动执行真实 Validation、Test 或模型训练。
