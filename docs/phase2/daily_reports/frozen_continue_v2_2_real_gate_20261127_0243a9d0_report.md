# Frozen CONTINUE v2.2 Real-Qwen Gate — Stage B

日期：2026-11-27（Asia/Shanghai）。执行提交：
`19c658bed5e55884b68c3ccaf4dc076ab209951d`，执行时工作区干净。
Run ID：`p2_continue_v22_real_20261127_0243a9d0`。

## Objectives / Final Gate

按用户“好的开始下一步”的授权，执行已冻结 v2.2 入口的真实五任务工程验收。
固定 T1–T5、Worker 0/1/0/1/0、每任务一次 Source Attempt、零自动重试、零替换、
零 Failover；工程门禁失败即停，不进入正式采集。

**最终 Gate：CONTINUE_ENGINEERING_GATE_PASS。**
五个任务均完成首个合格 Observation 捕获、独立 Restore、真正的
resume(stream=True) 到 final_answer 和原确定性 Verifier。五个任务的 Verifier
均为 true，accepted_y_continue 均为 1；有效失败、censored/invalid、未执行数均为 0。

这是五条非正式工程验收结果，不是正式训练集，也不证明模型泛化或风险预测效果。
formal_experiment=false、research_dataset_eligible=false、formal_collection_ready=false。

## Local Environment Work / Freeze

读取仓库规范、最近日报、v2.2 Stage A 报告、新旧入口和既有真实远端预检实现。
本轮没有修改生产源码、协议、Catalog、Split、Verifier、Prompt、模型参数或预算。
现有 vLLM 凭据和 CA 环境变量均已继承；没有申请云端 API Key、生成凭据或记录明文。
CA 可读、在有效期内且 Hash 与可信基准一致；专用公钥及 known_hosts 指纹匹配。
本地磁盘门禁通过；执行前 8000/8001 无监听。

| 固定输入 | SHA-256 |
| --- | --- |
| v2.2 统一清单 | 48ddf9fb43fbed19ac5f40bd6d14c6879a267464616b42673b9e34b36679dc11 |
| 有效 Catalog | 66ac8a5d56aaed2f7aafeac57dab918cc46d4cc3d3c8287ec60686594f16682a |
| Split | baf287b2e5103432b9411eef5d89099707f6998ef01c51a2212eaaa92043e6b9 |
| 可信 CA | 97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef |

私有准备/派生审计目录：
`runs/phase2_continue_v22_preflight/p2_continue_v22_stage_b_20261127_0243a9d0/`。
真实原始 Run：
`runs/phase2_continue_acceptance_v2/p2_continue_v22_real_20261127_0243a9d0/`。
所有目录均为新的唯一 ID，未覆盖历史产物。

## Remote Environment Work

唯一一次 SSH Remote Command 完成只读预检，退出码 0，进程已回收，耗时 81.1722 秒。
使用原专用密钥、严格 known_hosts、BatchMode、禁止密码/键盘交互、ClearAllForwardings
及禁用连接复用；使用既有 Windows TCP Proxy，仅作字节转发，认证和加密由 WSL SSH 完成。
没有额外执行 SSH 探路、TCP 重试、Worker 重启、模型下载或远端目录创建。

远端核验完成时间：2026-11-27T10:35:32.682472Z。
容器为 36f825bcc5da、Ubuntu 22.04、root；当前 Listener、进程 start identity、
命令摘要、部署 state 和前后两次进程记录一致，所有权核验通过。

| Worker | 现场 PID | 现场物理 GPU | Remote Endpoint |
| --- | ---: | ---: | --- |
| worker-0 | 382420 | 0 | 127.0.0.1:8000 |
| worker-1 | 382422 | 1 | 127.0.0.1:8001 |

GPU 0/1 是本次现有服务的观测映射，不是新的固定分配或“空闲卡”声明。
只查询本项目现有映射，两次 GPU 采样间隔约 79.02 秒，没有查询或抢占其他 GPU。
模型 config、索引及五个权重分片均现场计算 SHA-256，与冻结基准一致。
服务版本为 vLLM 0.34.0、torch 2.14.0+cu132、transformers 5.22.2。
远端 CA Hash 与本地可信基准一致；没有发送 Worker 修改、停止或重启命令。

## TLS / Authentication / Worker Routing

使用冻结 ConnectionLifecycle 创建两条本次拥有的隧道。沿用已验收的
-F /dev/null 配置隔离及每 Worker 单个显式 LocalForward，未加载历史转发规则；
未声称隧道同时使用会清除显式转发的 ClearAllForwardings。

两个 Endpoint 均完成严格 CA/hostname TLS 验证，携带原凭据的 /v1/models
返回预期 Qwen/Qwen3-8B 身份；TLS 预检延迟分别约 0.1509 / 0.2112 秒。
未关闭 TLS 验证，未替换 CA，未执行无鉴权对照或额外探测。

真实 CLI 启动前再次验证本次证据的时间、唯一 Run 绑定、两个 Worker 和容器身份。
全部任务工具在本地 WSL 的隔离文件/SQLite 环境执行，远端仅提供模型推理。

## Work Completed / 五任务结果

| 任务 | Worker | 捕获 step | Source / CONTINUE 请求 | 最终 answer 类型 | Verifier / Y | 任务 wall 秒 |
| --- | --- | ---: | --- | --- | --- | ---: |
| T1 p2_data_region_units_sum_001 | 0 | 1 | 1 / 3 | object | true / 1 | 18.7771 |
| T2 p2_file_report_control_001 | 1 | 1 | 1 / 3 | string，精确含末尾换行 | true / 1 | 9.6509 |
| T3 p2_api_process_control_001 | 0 | 1 | 1 / 3 | object | true / 1 | 7.2113 |
| T4 p2_logic_chain_control_001 | 1 | 1 | 1 / 3 | boolean | true / 1 | 5.0747 |
| T5 p2_data_region_units_sum_002 | 0 | 1 | 1 / 3 | object | true / 1 | 17.1115 |

五个任务均在第一次合格 Observation 后停止 Source，不是第二次 Observation 验收规则。
Source 请求共 5 次，独立恢复后真实 CONTINUE 请求共 15 次；没有执行 B2 的额外
“受控下一请求”来替代完整 CONTINUE。全部终止方式为 final_answer。

T1 本次真实 final_answer 直接返回字典，原 Verifier 通过；没有转换自然语言、
替换模型答案或修改旧标签。T2 实际最终字符串与原 expected 精确相等，末尾换行
保留，证明本轮精确文本路径通过了真实模型验证。
T3 在分支内执行 update_order 后，以新的 Tool Call ID 再次 get_order，实际读到
processing / version=2；幂等写没有被重放，合法新只读查询未被误拦截。

五个 Checkpoint 的六类 Hash 均通过；实验结束后另行只读 verify_v2 和 inventory
检查再次通过。五个首次真实恢复请求 input_hash 均直接匹配对应 Checkpoint 的
next_model_input_hash。Source 不变、Checkpoint 不变、工具 lineage 完整、Replay=false。

## Provider Audit / Budgets

20 个真实 Provider Response 与 20 个 Parsing Outcome 按唯一 audit_request_id
完整关联，Parsing 全为 success。20 个 SDK 可见原始响应 Hash 均复核匹配，
Usage 完整，Finish Reason 全为 tool_calls，没有 length 截断或模型服务失败。
服务端未提供的 header request ID 保留 null；本地 Audit ID 和 Provider Response ID
用于关联，不伪造缺失字段。

| 任务 | Prompt tokens | Completion tokens | Total tokens |
| --- | ---: | ---: | ---: |
| T1 | 11,323 | 410 | 11,733 |
| T2 | 7,680 | 178 | 7,858 |
| T3 | 7,398 | 151 | 7,549 |
| T4 | 6,909 | 100 | 7,009 |
| T5 | 11,334 | 413 | 11,747 |
| 合计 | 44,644 | 1,252 | 45,896 |

20 次实际委托 Completion 上限均为 1024，最大单次实际 Completion 为 223。
单次 Context 均不超过 8192，每任务累计 Tokens 不超过 50,000，任务 wall 不超过
900 秒，Model/Tool Calls 在原预算内；Source 已消耗预算被继承。
Seed、Temperature、thinking 设置及生成配置保持冻结值，20 条生成配置 Hash 一致。
Provider 延迟累计 57.0045 秒；完整真实 CLI 单调耗时 61.9841 秒、退出码 0，已回收。

## Resource Release

- 五个任务共 10 个 SDK 客户端全部关闭，close_errors=0；API 预检客户端由原 finally 关闭。
- 两条本次隧道均退出 0、已回收；最终审计为 PROCESS_EXITED / LOCAL_PORT_CLOSED。
- 本地只读 /proc/net/tcp 与 tcp6 检查确认 8000/8001 无监听，没有额外 TCP 探测。
- 从本次三份 SSH stderr 提取 Windows Proxy 的 PID/start time，执行本地只读进程
  查询：三个原实例均 ABSENT，没有额外终止进程，也没有清理其他历史资源。
- 没有在完成后另发服务器 SSH/模型 HTTP/Completion。结束后的远端 PID 状态未额外查询，
  不把未观测状态表述为已现场复核。

## Artifacts / 历史完整性

真实 Run 原 Artifact Manifest 的 **260 个文件**长度与 SHA-256 全部匹配，
文件集合与清单精确一致。Source/Checkpoint/模型/工具/Verifier/预算/连接释放
原始记录均保留。派生检查单独保存在私有准备目录，未改写真实 Run。

本轮前 **11,021 个历史文件**和 **355 个 Git 跟踪文件**的 SHA-256 均未变化。
原 v2.1 失败 Run 及标签 0 保持原样，新结果不用于事后重标。
关键派生证据包括 local_preflight.json、remote_preflight_process.json、
remote_evidence.json、gate_process_result.json、real_gate_final_integrity.json、
proxy_release_query.json 和 local_port_release.json。

## Commands and Tests

唯一生产调用通过私有监督程序执行如下参数形状：

```bash
.venv/bin/python scripts/run_frozen_continue_acceptance_v2.py \
  --mode real-five-task-gate --authorize-remote \
  --run-id p2_continue_v22_real_20261127_0243a9d0 \
  --remote-evidence <本次私有证据.json> --connection-spec <本次私有连接配置.json>
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

运行后使用本轮目录内 offline_test_guard.py 执行原冻结入口、CONTINUE Outcome，
以及新版完整五任务/实际模型输入保真/CLI/版本隔离/生产工厂 Mock 等相关回归：
**53 passed，0 failed，0 skipped，20.45 秒**。日志为 tests_post_gate.log，
结构化结果为 test_results.json。该阶段禁止真实网络/远端工具；urllib3 的本地
IPv6 支持检测 bind 被阻断。回归没有追加真实模型请求。
生产代码未修改，因此未重复 Stage A 的 1061 项全仓测试。

## Status / Remaining Work / Git Commit

**Status: PASS（真实五任务工程验收）。**
新版 Source→Checkpoint→独立 Restore→完整 CONTINUE→原 Verifier→审计/释放闭环
已获真实 Qwen 验证。本轮不进入正式采集或模型训练，不宣称五条正例可构成风险数据集。
下一步应审核正式采集计划及其任务/故障覆盖、采样和数据质量门禁。

本轮只新增本脱敏报告；编译、差异检查和敏感信息扫描通过后按 AGENTS.md 第 15 节
自动 Commit / Push。私有 Run、原始日志、凭据、证书和模型文件均不上传。
GitHub 交付使用的网络连接单列为代码交付，不计作服务器实验连接。
