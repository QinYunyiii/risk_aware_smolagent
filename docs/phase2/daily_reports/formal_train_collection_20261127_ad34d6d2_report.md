# Formal Train Collection — 2026-11-27

## Objectives

按用户批准的 `formal_collection_candidate_v1.yaml` 完成正式生产者和本地资格验收后，
执行 Train 96 个一次性 Source attempts，以及预选的 19 个同 Checkpoint 独立重复。
保留 F6 分配调整和 F5 可见 no-progress；不改变任务、Split、Prompt、Verifier 或预算。

**结果：`TRAIN_COLLECTION_BLOCKED`。正式采集已开始，但未完成。**
在第 79 个任务触发审计完整性停批；78 个有效主样本和 13 个有效重复分支保留，
未发布训练集资格索引。不能宣称 96+19 完成，也不能将空标签补为 0。

## Frozen Execution Identity

- Run ID：`formal_train_20261127_ad34d6d2`。
- 执行代码：`e934c71ed381d680877d3373121b6b94391b4bb7`，启动时工作区干净，已推送 GitHub。
- Protocol：`risk-continue-v2.2.0`，正式实现修订 `formal-producer-v1`。
- 正式 Manifest：`553ce3c15b34588889775f7c95cadd5ece94219e1f45b18b165e4858a1c82d08`。
- Parent Manifest：`48ddf9fb43fbed19ac5f40bd6d14c6879a267464616b42673b9e34b36679dc11`。
- 正式计划 SHA-256：`1da6c9f8fd5b5fa9142a2f49f743b70adcaa1db99cb3b6119e13e8433f7319da`。
- 单次 Completion 1024、Context 8192、Seed 20261118、Temperature 0、thinking=false；
  每 Source+主 CONTINUE 模型调用最多 10、工具调用 12、总 Token 50000、墙钟 900 秒。
- 每个重复独立继承同一 Checkpoint 的 Source 剩余预算；重复不作为额外独立训练行。
- 本轮只有 Train；Validation/Test 请求为 0，不进行风险模型训练。

## Local Environment Work

正式生产者、18 个特征溯源、故障恢复及追加式日志的实施详见
[Stage A 报告](formal_train_producer_stage_a_20261127_report.md)。

执行前本地专项 15 passed，全仓 **1088 passed，0 failed，0 skipped**；
最终离线生产入口完成 96+19，5234 个 Fixture 文件 Hash 全部匹配。Fixture 始终是
非正式数据，不导入本次正式 Run。compileall、diff check 和敏感信息扫描通过。

确认当前进程继承原项目 API Key 和 CA，严格证书链/hostname 验证开启；只记录存在性和
CA SHA-256，不输出或复制凭据值。原专用 SSH 身份和严格 known_hosts 指纹核验通过，
8000/8001 本地端口初始未占用，可用磁盘约 939 GiB。

## Remote Environment Work

一次只读 SSH 预检于北京时间 20:41:11–20:42:32 完成，耗时 81.112 秒，退出码 0。
容器、root 身份、Ubuntu 22.04、远端 CA、现有 Worker 进程归属、两次间隔 GPU 采样、
7 个模型权重文件 Hash 及模型软件版本均通过。实际核验的已有服务映射为 Worker 0 →
物理 GPU 0/PID 382420，Worker 1 → GPU 1/PID 382422；这是本次观测，不作为以后固定映射。

复用现有 Qwen3-8B 服务，未启动/重启 vLLM、未分配或抢占 GPU，未修改远端文件或服务。
随后创建本次拥有的两个独立 SSH LocalForward，严格 TLS 和两个 `/v1/models` 鉴权/身份
预检通过。Source 和其 CONTINUE/重复始终使用计划指定的同一个 Worker，Failover 为 0。

正式进程北京时间 **20:42:52–20:54:48** 运行，715.577 秒，停批退出码 1，非超时，已回收。
本轮合计 1 次只读预检 SSH 和 2 个受控隧道进程，均无自动重试。

## Results

| 项目 | 实际结果 |
| --- | --- |
| 计划主任务 | 96 |
| 已执行一次 Source | 79 |
| 捕获 Checkpoint / 完成特征冻结 | 79 / 79，全部首个合格点 step 1 |
| 有效主标签 | 78：72 个 `Y_continue=1`，6 个 `Y_continue=0` |
| 空标签 | 1，审计不完整，排除在风险样本之外 |
| 尚未尝试 | 17，未补任务、未替换 |
| 预选重复 / 实际有效重复 | 19 / 13；6 个尚未执行 |
| 重复与对应主标签一致 | 13/13，仅记录配对结果，不当作独立样本扩样 |
| 真实 Provider 请求 | 286：Source 79、主 CONTINUE 176、重复 31 |
| Provider 返回 / Usage 完整 | 286/286；所有返回均保存原始 SDK 响应和 Hash |
| Parsing 审计 | 285 条 success，1 条缺失，不能宣称 100% 完整 |
| Prompt / Completion / 总 Token | 631800 / 15520 / 647320 |
| 最大观测 Completion | 295，未超过固定 1024 |
| 有效主样本的六类 Hash / 首次模型输入 | 78/78 全通过 |
| 有效主样本 Source/Checkpoint 不变及 Replay Guard | 全通过，无不安全重放 |
| 自动重试 / Worker Failover / 新 Worker | 0 / 0 / 0 |

6 个有效失败为 `api_order_pending_read_001/002`、`api_order_processing_read_001/002`、
`api_order_start_processing_001/002`。它们均完成真实 CONTINUE 和确定性 Verifier，
以 `verification_failure` 保留为 0；不是基础设施中断，不为提高成功率而修正结果。

当前有效主样本的观测故障覆盖：data_analysis、file_retrieval 已有 F0/F1/F2/F3/F5；
api_workflow 已有 F0/F1/F2/F4/F6；calculation_logic 仅 F0。F6 已观测 committed/unknown，
not_committed 的计划任务尚未执行。因此即便有效数量 78 超过最低 72，整批完成条件和
故障覆盖条件仍未满足，`quality_passed=false`，无 `train_release_*.json`。

## Stop Evidence and Root Cause

停止任务：`p2_api_process_invalid_001`，F3 目标 `update_order`，非预选重复。
该 Source 第 1 步捕获后，Agent 在主分支中持续查询/提交原被拒参数，最终触达 10 次
总模型调用预算（Source 1 + CONTINUE 9）。预算保护按冻结规则阻止继续执行工具。

原始证据明确区分如下事件：

1. 第 9 个 CONTINUE Provider 响应已返回，finish_reason=`tool_calls`、Usage 完整，
   3108 prompt + 59 completion tokens，耗时约 2.834 秒；不是服务超时或 TLS 失败。
2. 该响应的 Audit ID 为 `local-405f275a-d0e2-4fe2-a7bc-30085525955a`，原始响应 Hash 为
   `083f122cbf92fb609e5f899e0d7cc51fddb00700737ef99b970541b3de5c6c65`，但没有 Parsing 配对。
3. `checkpointable_tool_agent.py:98` 在上游首次 yield ToolCall 时才记录 Parsing；
   同文件 `:165–170` 的执行预算检查先于这个 yield。最终 Agent Memory 的 step 10
   记录 `AgentToolExecutionError: terminal_model_call_budget`，所以该响应没有走到记录位置。
4. max_steps 的最终兜底还产生一次本地生成尝试，RecordingModel 以
   `difference=public_tool_schemas` 拒绝，未调用 Provider。component 中
   `model_requests_added=10` 包含此本地拒绝，不能误报为 10 个真实分支请求或第 11 次网络调用。
5. 正式审计门禁检测出 10 个该任务 Provider 事件与 9 个 Parsing 事件不能配对；
   `FormalOutcomeEvaluator` 按 fail-closed 规则保存 canonical termination=`unverifiable`、
   `outcome_status=censored`、`y_continue=null`、无 Verifier Outcome。component 的
   execution termination 仍为 `budget_exhaustion`。不能据通用 failure_category 中的
   `environment_failure` 反推远端环境发生故障。

**已识别问题是预算边界上的 Parsing 审计覆盖缺口，不是新的网络故障。**
本次本地 Fake 的 F3 策略遇到参数错误后直接终止，没有模拟真实 Agent 循环直至预算
门禁的这条路径；这是本地验收覆盖不足。全仓测试通过并不能证明此边界已经被覆盖。

六类 Restore Hash、首次模型输入、Checkpoint 不变性及 Source 不变性在该空标签任务
也通过；真正未通过的是审计完整性。没有事后补写 Parsing、补造 Verifier 或将 null 改为 0。

## Evidence Integrity and Resource Release

原始数据仅保存在本地：
`runs/phase2_formal_train/formal_train_20261127_ad34d6d2/`。
启动证明、远端原始记录、监督进程记录、离线核验和问题取证位于
`runs/phase2_formal_train_preflight/formal_train_preflight_20261127_ad34d6d2/`。

- Run 原始 Manifest 覆盖 **4325 个文件**，逐一 SHA-256/长度匹配；Manifest SHA-256 为
  `8b9b4c938cec15610fb0eb7186bbdad6d327a5a9603b54ac252885fff11ccd00`。
- 79 个 completed journal 的不可变文件逐项匹配；79/79 特征从原始决策数据重算相同，
  特征落盘时间早于该任务任何 CONTINUE/重复请求。Task/Checkpoint/Outcome/Risk Hash 链通过。
- 本任务开始前的 11327 个历史文件、362 个既有跟踪文件均未改变，所有父协议输入不变。
- 两个本次 SSH 隧道退出码均为 0，状态 PROCESS_EXITED / LOCAL_PORT_CLOSED；本地
  `/proc/net/tcp{,6}` 复核两个端口释放。3 个 Windows TCP Proxy 的原始 PID/启动身份
  均已不存在，未终止其他进程。所有本次模型客户端已关闭。
- 结束后的取证仅本地读文件和 Windows 本地进程元数据，新增实验室连接/模型请求为 0。
  没有通过额外远端连接声称服务仍健康；依据本轮操作记录可确认未操作远端 Worker。
- 当前 Run/预检产物未发现继承的真实 API Key 明文；原始轨迹、证书和权重不上传 Git。

## Remaining Work and Minimum Next Step

保留本 Run 的原始 `TRAIN_COLLECTION_BLOCKED` 及该任务 null。17 个未开始任务与 6 个未执行
重复按原排程封存；不绕过 journal 停批检查，不启动新 Run 偷换剩余队列，也不重跑第 79 项。

下一步只建议独立的离线最小修复：为“已返回响应在工具执行前被预算保护拦截”的路径
记录真实解析/被跳过状态及原因，不能伪造 parsing success，不能移除预算门禁。增加 Source+多轮
CONTINUE 至模型预算边界、工具预算边界、最终兜底 SDK 前拒绝和 0/null 区分的 Fake 端到端测试。
代码改动需发布独立实现修订和 Hash；不能改本 Run 的已冻结实现、原始结果或父 Manifest。

完成离线修复、回归和证据审核后，再单独确定剩余队列的版本/lineage/续跑授权；当前拒绝
自动续跑、自动补采或历史标签重算。Validation/Test、训练和正式数据集发布均未开始。
现有 78 行不满足后续 LightGBM 最低 200 Train 行、每类 30 的门禁，重复行不能用于凑数。

## Tests and Git

执行前全仓 1088 passed；执行后的 SHA/记录链/特征重算及资源释放离线核验通过。
停批后专项回归 **15 passed，0 failed，0 skipped**，37.29 秒，审计钩子禁止真实网络；
compileall 和 diff check 通过。冻结生产代码未改变，未重复整个全仓回归。

实际执行入口及本地收尾命令：

```bash
.venv/bin/python runs/phase2_formal_train_preflight/formal_train_preflight_20261127_ad34d6d2/run_remote_preflight.py
.venv/bin/python runs/phase2_formal_train_preflight/formal_train_preflight_20261127_ad34d6d2/run_formal_train_once.py
.venv/bin/python runs/phase2_formal_train_preflight/formal_train_preflight_20261127_ad34d6d2/verify_local_release.py
.venv/bin/python runs/phase2_formal_implementation/formal_stage_a_20261127_9fc537e4/offline_test_guard.py tests/test_formal_train_collection.py -q
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

监督入口的实际生产 CLI argv 保存在预检目录 `gate_argv.json`，包含正式授权开关、
当前版本的本地资格 receipt、历史真实工程 Gate 及本 Run 的新鲜远端证据绑定。
真实生产进程退出码 1；上述 15 项本地回归退出码 0，两者不能混淆。

Git 交付仅新增本脱敏日报，原始实验文件保持本地且不提交。本报告的实验状态始终
BLOCKED，不因单元测试通过而改为 PASS。代码交付 Commit 为前述 e934c71；报告提交
按仓库自动交付规则进行，报告 Commit 见 Git 历史。

## Status: BLOCKED

正式生产者已交付，真实 Train 已启动并按审计停批；78 个有效主样本、13 个有效重复
已安全保留，但 96+19 和整批质量门禁未完成。等待审核后再处理离线修复及后续执行。
