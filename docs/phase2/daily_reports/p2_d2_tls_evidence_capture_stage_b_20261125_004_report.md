# P2-D2 TLS Evidence Capture — Stage B / Final Authorized Run 004

## 1. 执行摘要与最终 Gate

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_tls_evidence_capture_stage_b_20261125_004`。
最终 Gate：**TLS_EVIDENCE_CAPTURE_STAGE_B_PASS**。
实际 TLS Outcome：**SUCCESS**；冻结 Classifier：**REMOTE_TLS_SUCCESS**。

通过正式 CLI 完成唯一一次真实 SSH、一个 Remote Command 和一次 Worker 0 TLS 握手。
容器/用户、可信 CA、Worker 进程身份/命令摘要/监听归属预检通过，24 字段 Metadata
经 V1 完整传输并独立持久化。实际观察到 ClientHello、ServerHello、Certificate、
CertificateVerify、双向 Finished、完整 brief 建连摘要和 Verification: OK。
证书链和 hostname 验证通过，OpenSSL/SSH 均退出 0，未超时，收据完整，进程已回收。

OpenSSL 进程单调耗时 **0.013995164 秒**；本地 SSH/采集总单调耗时 **24.112980597 秒**。
原始两流分别 4,830 / 704 bytes，Wire 18,409 bytes，SSH Client Log 4,526 bytes。
执行后全仓 **692 passed in 45.99s，0 failed，0 skipped**。

没有重试、Failover、Tunnel、HTTP、Qwen、Agent、GPU/vLLM/Docker 或远端文件管理操作。
本轮没有修改已验收代码、冻结参数、历史 Run 或 Fixture；未 Commit/Push。

## 2. 授权、入口与唯一执行

执行使用用户本次 Final Authorized Real Worker-0 TLS Verification 授权。
固定 004 目录执行前不存在，没有换号或复用 002/003。
正式命令只执行一次，没有注入 Fake 工厂，也没有另写 SSH 执行器：

```text
.venv/bin/python scripts/run_p2_d2_tls_evidence_capture_stage_b.py --mode execute --authorize-stage-b --run-id p2_d2_tls_evidence_capture_stage_b_20261125_004 --session-id cccd13ec33464a749489f62dc1b4c820
```

CLI 退出码 0；原 gate_summary.json 记录 real_ssh_attempts=1、ssh_factory_calls=1、
legacy_summary_path_called=false、error=null。核心采集、Metadata、退出收据和分类器
均走已验收生产路径；Classifier 的后处理是已验证原始输出的离线回放，不是第二次请求。

同名 `p2_d2_tls_evidence_capture_stage_b_report.md` 属于历史 001，本次用独立 004 文件名
保存正式日报，保留历史报告及其哈希，不把旧 BLOCKED 改成 PASS。

## 3. Git 与实现冻结

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支 main。
工作区已有 Stage A 未提交文件全部保留。执行前与上一轮 Stage A 的验收后文件哈希
逐项对照通过，全部源码/测试/配置及原有未跟踪文件结束时仍与本轮基线一致。
完整 Git 状态和配置 SHA-256 保存在 local_preflight/baseline.json 及最终完整性记录。

已核对本次指定的四份报告、正式 CLI、tls_stage_b.py、三个核心模块及实际调用关系。
没有 reset/clean/checkout/stash，没有修改 Metadata Schema、V1、TLS Classifier、CA、
OpenSSL 参数、Checkpoint v2、B2/B3/B4 或 1024-token 冻结配置。

## 4. 本地前置检查

- CA 文件存在且可读，哈希与冻结值匹配；没有复制或输出证书内容。
- 专用密钥文件可读、权限私有，公钥指纹与可信记录一致。
- known_hosts 中本次目标 Host Key 的指纹与可信基准匹配。
- 实际 argv 的本地 ssh -G 展开确认端口、用户、严格认证、禁用复用和全部转发。
- 已验收实现、历史 Manifest、TLS 指纹、磁盘/目录可写性检查通过。
- 连接前相关离线测试 **333 passed in 7.97s**。
- 正式采集器在工厂之前建立/fsync Metadata 期望、独立输出预留、生命周期和 Manifest pending。

准备目录与本次 Run 同 ID，所有准备文件随后复制到 Run 的 local_preflight/，没有接管历史目录。
本地 OpenSSH：OpenSSH_9.6p1 Ubuntu-3ubuntu13.20，所链接 OpenSSL 3.0.13；这不是远端版本。

本地审计脚本第一次假设 -G 必定输出 controlpath 键，出现 KeyError；当时真实连接=0。
实际显式 ControlPath=none 在该 OpenSSH 的 -G 输出中省略。按“无路径且显式 none”核验
禁用状态后通过，仅修正本地审计读取方式，没有改生产代码或 SSH 参数。
这两次 -G 和 -V 是本地配置/版本查询，不建立 Session；连接型 SSH Client 仍只有 1 个。

## 5. SSH 身份与实际参数

Alias：risk-agent-remote；目标 root@121.48.161.243:4035。
专用公钥：SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A。
Host Key：SHA256:g8sbLpuIhU3e/BydNugmhs+VMXbJ6fuqUEwqmsTUESM。
原始日志确认 publickey 认证、Host Key 匹配和 Exit status 0。

实际 argv 完整保存在 run_manifest.json / process_result.json，二者已精确核对一致：

```text
/usr/bin/ssh -T -v -E <Run004>/ssh_client.log.raw
  -i /home/three/.ssh/risk_agent_audit -p 4035
  -o UserKnownHostsFile=/home/three/.ssh/known_hosts
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
  -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectionAttempts=1
  -o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none
  risk-agent-remote "python3 -B -"
```

没有 -F /dev/null、-L/-R/-D、ProxyJump/ProxyCommand 或历史连接复用。
原 builder 生成的 prepared 意图事件仍为 ConnectTimeout=10；已验收生产入口在实际
工厂中按当前授权设置为 15，最终 argv 与 -G 均确认 15，未改 builder 默认值。
TLS 无需 API Key；SSH 及远端工具子进程只继承 PATH/LANG/LC_ALL，未传输模型认证凭据。

## 6. 容器、CA、OpenSSL 与 Worker 所有权

| 项目 | 本次证据 |
| --- | --- |
| 容器/hostname | Metadata 中均为 36f825bcc5da |
| 用户 | Metadata 中 root；预检实际 uid=0 门禁通过 |
| OS | 发送源码的 Ubuntu 22.04 门禁通过；没有单独导出 os-release 内容 |
| OpenSSL | /usr/bin/openssl，OpenSSL 3.0.2 15 Mar 2022，Library 同版本 |
| 原 CA | /root/.config/risk-agent/tls/ca.crt，实际读取成功并核对哈希 |
| CA SHA-256 | 97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef |
| Worker | worker-0，当前验证 PID=382420 |
| Process Identity | start ticks=519951147 |
| Command Identity | vllm-worker-0，安全身份标签，无完整参数原文 |
| Command Fingerprint | a4acce03f6a6bf66db69fcf3fe9748f36ed82f076d28e8eb67104e415b8e1e34 |
| Endpoint | 127.0.0.1:8000，监听 socket 由该进程持有 |
| Ownership Verified | true；preflight_status=PASS，preflight_error=null |

待验证 PID 来自校验过的历史权威审计，而非本轮在代码中写死“该 PID 当前可信”。
当前身份依据是重新读取 /proc 后的 start identity、完整命令摘要、运行状态和
Listener socket 归属联合匹配；start ticks 前后采样一致，不能仅凭 PID 存活通过。
基准来源及 SHA 保存在 run_manifest.json / local_preflight.json。
没有单独保存当前 socket inode 数值，不以历史 inode 冒充新观测。

直接 Metadata 观测与控制流证明分开：容器、版本、CA、Worker 字段直接保存在 V1
记录；OS/uid/监听检查的详细步骤由固定源码、其哈希及成功门禁共同证明。
这些检查不证明模型 Completion 健康，也没有查询 GPU 或 Worker 1。

## 7. Metadata 完整性

Session ID：`cccd13ec33464a749489f62dc1b4c820`，与 Run、V1 nonce、期望记录匹配。
24 字段 Metadata 均完整，Worker 字段与已验证期望匹配；远端实际生成的两项时间戳保留。

- Wire 中规范化 Metadata SHA-256：
  52020804261802491633a8d7b1db7b1bbd7b3c7a7accc10dbcb2c5ed27182efd。
- remote_preflight.json：1,068 bytes，含结尾 LF；文件 SHA-256：
  4b847ae31c0b06941faa1d977b9475002af7c0ee4ba6c55a02e3dd97cac0e570。
- tls_preflight_metadata.json 与 remote_preflight.json 逐字节相同。
- 独立文件、原始 Wire、Metadata Hash、核心 Manifest 全部重新验证通过。

本地期望明确标记 LOCAL_EXPECTATION_NOT_REMOTE_OBSERVATION；本轮没有用它替代远端记录。
Metadata 不在 OpenSSL stdout/stderr、SSH Client Log、Agent Memory 或 Checkpoint 中。

## 8. 冻结 TLS 参数与执行次数

实际 OpenSSL argv 来自可信退出收据；仅把原 openssl 名称解析为已核验的绝对路径：

```text
/usr/bin/openssl s_client -connect 127.0.0.1:8000 -servername localhost
  -verify_hostname localhost -verify_return_error
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

参数、SNI、CA、hostname/证书链验证及 5 秒总进程预算未改；没有强制切换 TLS 版本或
密码套件。本次协商结果为 TLSv1.3 / TLS_AES_256_GCM_SHA384。

执行次数必须分清：**1 个真实握手 OpenSSL 子进程**，另有远端只读 version、s_client -help
元数据子命令各 1 次，不建立 TLS。后二者计数来自固定程序与完整预检成功控制流，
没有独立采集其 PID/wait 收据；握手子进程则有完整真实 V1 wait 收据。
因此 OpenSSL 程序调用总计 3，握手次数为 1；没有隐瞒元数据命令或误写成所有调用共 1。
OpenSSL stdin=DEVNULL，没有 HTTP 应用请求。

## 9. TLS 结果与完成依据

| 字段 | 本次实际结果 |
| --- | --- |
| TLS Handshake Started | true |
| ClientHello Observed | true |
| ServerHello Observed | true |
| Certificate Observed | true |
| Certificate Chain Verified | true |
| Hostname Verified | true |
| Finished Observed | true，服务端接收与客户端发送均存在 |
| TLS Handshake Completed | true |
| TLS Alert Observed | true，本端 warning close_notify |
| Fatal Alert Observed | false |
| OpenSSL Exit Status | 0 |
| OpenSSL Timed Out | false |
| Classifier Result | REMOTE_TLS_SUCCESS |
| TLS Outcome | SUCCESS |

原单一 SSL negotiation finished successfully 标记仍未出现。本次没有更改分类器去补它，
而是实际保留了完整 CONNECTION ESTABLISHED、Protocol version、有效 Ciphersuite 和
Verification: OK；冻结 Classifier 的既有 brief 完成分支满足，并有双向 Finished 佐证。
不是仅凭 Verification: OK、exit=0 或短耗时推断握手完成。
Hostname true 的依据为可信 argv 中严格 -verify_hostname localhost / -verify_return_error
与验证成功联合证据，没有声称 OpenSSL 另输出了一条独立 hostname-success 行。

## 10. 实际阶段时间线

下列时间取匹配行末尾所在 V1 data 帧的远端父进程读块观测，基于同一远端单调时钟，
相对 OpenSSL 工厂提交；不是抓包时间，也不是精确 TLS-only 时长。

| 事件 | 相对提交 ms |
| --- | ---: |
| ClientHello Sent | 8.017 |
| ServerHello Received | 10.288 |
| Certificate Received | 10.654 |
| CertificateVerify Received | 11.312 |
| Server Finished Received | 11.476 |
| Client Finished Sent | 11.554 |
| Complete Brief Summary | 11.602 |
| Certificate Verification OK | 11.653 |
| close_notify 输出观测 | 11.774–11.809 |
| OpenSSL 总进程结束 | 13.995 |

两处 Alert 日志是关闭通知的输出观测，不当成两次握手或 fatal 拒绝。
未测量精确 TCP connect 时长。Classifier replay_events 的时间是本地回放时间，
没有冒充远端实际阶段时间；真实块时间和偏移见 tls_stage_timeline.json。

## 11. 原始文件与逐字节验证

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| openssl_stdout.raw | 4830 | 78ca1c7934c807d9baef9de79ebe2368d24023339e871835a9209b1353198361 |
| openssl_stderr.raw | 704 | 5333132919c6e26b20822d351f633a3179bd3b7e829e34b383f7b6e883596847 |
| ssh_client.log.raw | 4526 | 40effe3fdd644ef3d967e1483af26f9701c15472c834bc1be054b6a90aa7462f |
| ssh_wire_stdout.raw | 18409 | 8f2e5e54b7478b0fe6a83a5440ae06d6d8d08e3baa699c1b42dd2355a172f527 |
| ssh_process_stderr.raw | 0 | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| remote_exit_receipt.raw | 1223 | 878c4de9cd54819a6268011ccd616dc0c67b081577f92af1f302b972eb7af240 |

公共 raw 文件与原采集器 stock 文件逐字节相等；原始两流合计 5,534 bytes，不等于
含 Metadata、帧头及收据的 18,409 Wire bytes。退出帧直接按原 Wire 行 bytes 保存，
没有重新编码。没有过滤、改写、静默截断或把本地 stderr 当成远端 OpenSSL stderr。
TLS 内容保持私有原始证据，不在本日报打印证书或会话材料，不自动上传。

V1 共 22 帧；本地统一观察时间线 41 项，sequence 连续、monotonic_ns 严格递增，
UTC、chunk SHA、offset、累计字节全部匹配。时间线代表读取观测顺序，不是远端 write 顺序。

## 12. 退出收据、耗时及资源释放

SSH：PID=1188238，start identity=38486156，rc=0，reaped=true，timed_out=false。
OpenSSL：PID=404757，start identity=573206888，rc=0，reaped=true，timed_out=false。
SSH Transport=SUCCESS，Remote Command=COMPLETED，远端退出状态 0 来自 V1 收据，
不单凭 SSH 返回码推断。收据的 argv、两流大小/SHA 及实际 wait 生命周期均完整。

本地 UTC 开始 2026-11-25T13:17:29.550488+00:00，结束 13:17:51.721642+00:00，
对应北京时间 21:17:29.550488–21:17:51.721642。单调总耗时 24.112980597 秒，未超过 30 秒。
远端 OpenSSL UTC 开始 13:17:37.037915，结束 13:17:37.051896，单调耗时 0.013995164 秒。
本地 UTC 差值与单调耗时存在约 1.942 秒差异，保留原值，原因本轮未调查，预算以单调
字段判断；不跨主机相减单调时钟，不把 SSH/持久化总耗时当成 TLS 握手耗时。

本地进程自然退出并 wait/reap，复核 /proc/1188238 已不存在；无需向已退出 PID 再发
terminate，避免 PID 复用误杀。原句柄/start identity 为本次进程的所有权依据。
本地和远端 terminate/kill 均未触发。远端结束由 V1 收据证明，未另开 SSH 去检查。
清理时间、退出观察时间和无残留核验见 resource_release_audit.json。

没有 Tunnel 或端口转发，没有关闭历史 ControlMaster，没有 Worker/GPU 服务管理或
远端文件写入。没有额外执行后置远端探测，不能声称另行取得 Worker 运行后快照。

## 13. Manifest、历史保护与产物目录

Run：`runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_004/`。

正式入口原始 artifact_manifest.json 的 12 项、CLI 追加 artifact_manifest_post_analysis.json
的 27 项均通过重读校验，原样保留。操作后补充的 TLS 标志/实际块时间线、释放核验、
预检副本、回归记录及日报另由 artifact_manifest_final_audit.json 统一覆盖，包括前两份
Manifest，排除自身。所有文件 fsync 后独占 pending/no-clobber 原子发布并逐项复核。
路径、大小、SHA、Run ID、类型、时间观察值及文件集合均核对；ctime 不冒充 birth time。

执行前后 **343 个历史文件、25 份历史 Manifest** 全部匹配，002/003 原始结果未修改。
源码、配置、测试、Fixture 和全部原有未跟踪文件保持本轮前哈希。
没有为了新 PASS 重新生成历史证据或修改历史 Classifier 结果。

## 14. 本地回归、扫描与副作用统计

- 执行前：333 passed in 7.97s。
- 执行后全仓：**692 passed in 45.99s，0 failed，0 skipped**。
- compileall（src/scripts/tests）、git diff --check、敏感材料扫描通过。
- 全部历史 Manifest、冻结配置和源文件前后校验通过。
- 本轮没有新的测试或实现变更；离线回归没有执行新的真实网络请求。

防护入口拦截 urllib3 导入期的本地 IPv6 bind 能力检查；没有建立连接。
Raw 文件不做有损脱敏，保持私有权限并禁止自动发布；扫描/报告不输出凭据或私密材料。
无真实 SSH/远端异常链，正式 CLI error=null；唯一审计脚本 KeyError 已在本地预检节说明。

```text
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_preflight/p2_d2_tls_evidence_capture_stage_b_20261125_004/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

真实 SSH=1、Remote Command=1、TLS 握手=1；自动重试=0、Failover=0、Tunnel=0、
HTTP=0、Qwen=0、Agent Task=0、GPU/vLLM/Docker/远端文件管理=0。
没有 Commit/Push/fetch。原始 TLS 产物属于非正式工程验证，不是风险模型训练标签。

## 15. 后续条件与停止

本次真实 TLS 工程前置 Gate 已通过，**可以提交审核，等待独立授权进入 Connection
Smoke Test**。尚未执行 SSH 转发下的 HTTPS 请求、/v1/models、模型身份检查或连续
90 秒稳定性监测；不能据此宣布 Smoke 或 Full Five-Task Gate 已通过。
本轮没有新增排查任务、重跑握手或继续工程重构。

最终 **TLS_EVIDENCE_CAPTURE_STAGE_B_PASS**。完成后停止，等待用户审核与下一阶段明确授权。
