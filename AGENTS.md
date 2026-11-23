# AGENTS.md --- Codex 开发规范

本文件规定 Codex 在本仓库中的工作方式。项目目标、架构、算法和每日任务以
`PROJECT_GUIDE.md` 为准。

## 1. 每次新会话开始前

1.  阅读 `AGENTS.md`、`PROJECT_GUIDE.md`、`README.md`。
2.  检查 `git status --short`，保护用户未提交修改。
3.  阅读 `docs/daily_reports/` 最近一份日报。
4.  确认当前 Day、前置 Gate 和遗留问题。
5.  判断当天需要本地 Docker、远程 SSH 还是 HTTPS API。
6.  向用户报告计划、修改范围和阻塞项。

必须通过代码、测试和产物核实完成情况，不得只依赖上次会话的口头总结。

## 2. 固定部署边界

### 本地电脑：Windows + WSL Ubuntu 24.04

-   Recovery-Bench 和现有 Docker；
-   MiniToolBench-R、smolagents；
-   本地工具执行、任务调度；
-   轨迹、故障注入、Checkpoint / Restore、分支实验；
-   风险预测、效用估计、策略和实验日志。

### 远程服务器：共享实验室服务器，8 × RTX 4090

-   当前 SSH 入口实际进入 Ubuntu 22.04 Docker 容器；宿主机操作系统未经审计；
-   容器可见 8 张 RTX 4090，但属于公共资源；
-   每次只从当时无人使用的 GPU 中选择两张，禁止抢占、终止或影响他人任务；
-   Qwen3-8B 权重位置必须实际核实；
-   在获准的两张空闲卡上运行两个独立 vLLM 实例；
-   HTTPS OpenAI-compatible API。

Worker 0 / 1 的物理 GPU ID 动态分配，不再固定为 GPU 0 / 1。启动前必须重新检查显存、利用率和实验室占用规则；容器 PID namespace 可能看不到其他容器进程，不能仅凭 `nvidia-smi` process 表为空判断 GPU 空闲。每次运行记录物理 GPU ID、检查时间和 Worker 映射。

本地 Agent 通过 HTTPS 调用远程模型。模型返回 Tool Call 后，由本地
smolagents 执行工具。不得误认为远程模型可直接访问本地 Docker
或文件系统。

除非用户明确批准，不得迁移 Recovery-Bench、在远程重建本地 Docker
环境、把 Qwen 权重下载到本地，或把本地工具改为远程执行。Harbor
位置必须以实际审计为准。

## 3. 远程访问与安全

-   只有 SSH 已配置或用户提供连接信息时才执行远程命令。
-   不猜测主机名、用户名、端口、路径、密码或密钥位置。
-   SSH 不可用时，提供待用户执行的检查命令并请求输出。
-   不在代码、配置、命令记录或日志中暴露 API Key、密码、SSH 私钥、TLS
    私钥。
-   API Key 从环境变量或安全密钥管理读取。
-   TLS 校验必须开启；不得用 `verify=False` 或 `curl -k` 作为正式方案。
-   不将无鉴权 vLLM 端口直接暴露公网。
-   不擅自重启共享服务、下载模型、占用 GPU 或终止进程。
-   不得使用已有显存占用或实验室标记为在用的 GPU；不得通过杀进程、reset GPU 或其他方式腾卡。
-   远程高资源操作前先说明资源影响并取得用户确认。

## 4. 研究设计变更控制

以下内容修改前必须说明原因并取得用户确认：研究问题、动作语义、风险标签、效用/成本公式、任务领域、故障类型、数据划分、Checkpoint
/ Restore 协议、基线、消融、评价指标和本地/远程职责边界。

允许不改变研究含义的代码重构，但须同步更新测试和文档。

## 5. 先审计再修改

-   检查代码、配置和调用关系，不根据文件名或 README 推断能力。
-   不假设 Recovery-Bench / Harbor
    支持快照、恢复和分支，必须检查实现并验证。
-   不直接修改 Recovery-Bench 原仓库，优先在本项目实现 Adapter。
-   不删除、重建或停止已有 Docker 容器。
-   不为匹配目录树创建无功能空文件。
-   修改外部仓库前必须说明影响和回滚方案并取得确认。

## 6. 本地与远程命令区分

执行命令前明确目标机器： - 本地 WSL：项目代码、Agent Conda 环境、Docker
任务、实验日志。 - 远程服务器：GPU、CUDA、模型权重、vLLM、TLS、API
服务。

不得在本地执行远程模型下载命令，也不得在远程服务器执行本地
Recovery-Bench 路径操作。SSH 命令前确认连接主机身份。

## 7. Worker Router 与模型客户端

-   Worker 0 / 1 使用独立配置和可追踪 `worker_id`。
-   Base URL、模型 ID、超时、CA Bundle 路径通过配置提供。
-   API Key 通过环境变量读取。
-   记录 Endpoint、Worker ID、模型版本、延迟和错误类别，不记录密钥。
-   模型服务故障与 Agent 任务失败必须区分。
-   Worker 切换不得导致有副作用的工具写操作重复执行。
-   两个 Endpoint 的模型版本、Prompt 和推理参数必须可追踪。

## 8. 代码质量

-   模块职责清晰，核心逻辑添加类型标注。
-   配置集中管理，不硬编码地址、端口和阈值。
-   对 API、Docker、文件系统和数据库操作进行异常处理。
-   随机过程显式设置并记录 seed。
-   写操作有幂等键、状态确认或其他安全保护。
-   Retry 设置次数上限和适用条件。
-   不将任务执行、模型请求、状态管理和指标计算塞入单一脚本。

## 9. 测试要求

每次修改后按影响范围运行单元、集成和必要的端到端测试。记录真实命令和结果；未运行的测试明确标记。

分布式调用验证： - 本地分别访问两个远程 Endpoint； - TLS 校验开启； -
Worker 路由和 `worker_id` 正确； - Tool Call 在本地执行； - API
超时和服务不可用有明确错误处理； - API 故障不被误记为任务环境失败。

状态管理验证： - Snapshot 覆盖必要状态； - Restore 后规范化状态一致； -
分支不共享可变文件、数据库或服务状态； - 同一 Checkpoint
的分支初始状态一致； - 结果由确定性验证器判定。

## 10. 数据与实验保护

-   原始轨迹和历史结果只追加，不覆盖。
-   每次运行使用唯一 `experiment_id`、`run_id`、`task_id`。
-   不删除用户数据、模型、日志或检查点。
-   Mock、fixture、手工样例和 smoke test 不得冒充正式实验。
-   派生数据必须追溯到原始记录、脚本、配置和版本。
-   Train / Validation / Test 按模板分组；同一 Checkpoint 分支不得跨
    Split。
-   特征只能使用决策时刻已知信息。
-   测试集不得用于阈值、特征、超参数或成本权重选择。
-   远程 API 中断期间未完成的任务不得自动记为 Agent 失败。

## 11. 长任务和资源管理

批量实验前说明任务量和资源，先运行 smoke test，检查本地磁盘、远程 GPU
和服务状态，确认输出目录与断点续跑。不要重复提交已完成任务。不得擅自杀进程或重启服务。

## 12. 每日工作流程

1.  阅读当天任务和前置验收。
2.  明确本地与远程分别做什么。
3.  检查 Git 状态。
4.  列出计划修改文件。
5.  实现最小可测试版本。
6.  运行测试并核对验收指标。
7.  生成 `docs/daily_reports/day_XX_report.md`。
8.  汇报遗留问题和状态。
9.  等待用户确认后进入下一天。

日报格式：

``` markdown
# Day XX Report
## Objectives
## Local Environment Work
## Remote Environment Work
## Work Completed
## Files Changed
## Artifacts Generated
## Commands and Tests
## Test Results
## Acceptance Checklist
## Known Issues
## Remaining Work
## Status: PASS / CONDITIONAL PASS / BLOCKED
## Git Commit
```

状态： - PASS：核心验收全部通过； - CONDITIONAL
PASS：非核心事项未完成但不阻塞； - BLOCKED：核心功能或前置条件未通过。

不得自行把 BLOCKED 改成 PASS。

## 13. 必须暂停并询问用户

-   需要改变研究设计或部署边界；
-   需要修改 Recovery-Bench 原仓库；
-   需要删除、覆盖或迁移数据；
-   需要管理员权限或系统级依赖；
-   需要下载模型、重启远程服务或执行高资源任务；
-   SSH/API 不可访问且需要用户提供信息；
-   前置条件不成立且存在多种方案；
-   实验异常，需要改变协议。

## 14. 最终原则

本地负责 Agent 执行和实验控制，远程服务器负责 Qwen3-8B
推理。代码能运行不等于实验有效；API
可访问不等于工具调用成功；日志存在不等于数据完整。所有完成声明必须有真实测试、日志或可检查产物作为依据。

## 15. 自动 GitHub 交付授权

当用户要求修改或实现任务，并且本轮验收测试通过时，Codex 获得以下授权：

1.  检查 `git status --short` 和 `git diff`。
2.  只暂存本任务相关文件，不提交用户无关修改。
3.  检查并排除密钥、密码、Token、证书、模型文件、未脱敏日志和大体积实验产物。
4.  运行影响范围测试、`compileall` 和 `git diff --check`；不适用的检查必须说明。
5.  测试通过后创建非交互 Git Commit。
6.  自动执行 `git push origin HEAD:main`。
7.  向用户报告测试结果、Commit SHA 和 Push 结果。

遇到以下情况必须停止并询问用户：

-   测试失败；
-   工作区存在无法与本任务区分的用户修改；
-   远端分支领先、出现冲突或 non-fast-forward；
-   需要 force push、rebase 或修改历史；
-   当前任务明确要求不 Commit 或不 Push；
-   检测到疑似密钥、隐私信息或不应上传的实验数据；
-   GitHub 分支保护要求 Pull Request。

禁止自动 force push、删除分支、创建 Tag、创建 Release 或覆盖历史。
