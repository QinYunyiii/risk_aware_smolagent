# P2-D2 TLS Evidence Capture — Stage B / 20261125_002

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_tls_evidence_capture_stage_b_20261125_002`。
最终 Gate：**TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**。

本轮停在本地连接前审计，真实 SSH=0、Remote Command=0、OpenSSL 进程=0、TLS 握手=0。
没有观察到本轮 TLS 成功、证书拒绝、握手超时或远端服务失败。
全仓离线回归 **577 passed in 43.77s，0 failed，0 skipped**。

实际阻塞：已验收的会话生产者没有独立预检元数据输入，泛用远端模板没有接通完整
TLS 预检与元数据发送路径。V1 底层能够表达附加控制元数据，不能把这个工程接线
缺口说成协议在理论上无法表达。当前任务第 15 节要求“如果发现必须修改代码才能
继续，立即停止并报告”，因此未临时修改生产者、注入替代 Encoder 或改写生成源码。

原 TLS Stage B 001 报告及原始 Run 保留。指定的同名日报已属于历史 001，故本次
使用带日期和 002 的独立文件，避免覆盖历史证据和基线哈希。

## 2. Git 与读取范围

- HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支：`main`。
- 工作区原有未跟踪文件保留，完整状态见本 Run 的 `baseline.json`。
- 阅读 AGENTS.md、PROJECT_GUIDE.md、README.md、Day 14 日报及本轮指定的五份 SSH/TLS 报告。
- 审计 tls_evidence_capture.py、remote_local_tls_isolation.py、ssh_evidence_transport.py、
  相关测试、现有离线 CLI、上次唯一 SSH 驱动及历史 Worker 只读检查源码。
- 本轮只新增本 Run 的离线审计/探针/测试记录和本日报；源码、测试、fixture、配置未修改。
- 不暂存、不 Commit、不 Push、不 fetch。

## 3. 阻塞的实际调用链

1. `remote_wrapper_source()` 复用现有函数生成 stdlib-only 模板，只定义
   `authorized_launch_after_preflight()`，不实现完整身份、CA、Worker 门禁。
   源码第 237–272 行明确要求调用方另行提供审核后的 bootstrap。
2. `authorized_launch_after_preflight()` 启动指定子进程，然后调用原 `produce_session()`。
   这条接口没有预检记录输入。
3. `produce_session(process, run_id, nonce, emit, clock=None, started_monotonic=None)`
   内部创建自己的 FrameEncoder，start 只调用 `encoder.start(**timestamps())`，发送时间字段。
   它没有 preflight、metadata 或 encoder 参数，exit 生命周期字段也按固定键构造。
4. 现有接收端保存完整 Wire，并通过权威 Decoder 还原两条 Payload；其
   `remote_observed_timeline.jsonl` 只记录白名单观察字段，不生成 `remote_preflight.json`。
5. 上次真实 SSH PASS 使用 `prepare_byte_probe()` 的固定字节程序，只验证固定容器/uid
   门禁、两条测试 Payload 和真实退出收据；并未验证 Worker/CA/OpenSSL 预检元数据链路。

关键区分：`FrameEncoder.start(**timestamps)` 实际可将附加字段传给权威 encode_frame，
FrameDecoder 也可把这些字段交给 sink；完整 Wire 本身不会丢掉它们。
因此不是“接收器必然丢失所有元数据”，而是当前已验收 producer/bootstrap 没有把本次
所需预检记录送入该路径。后续从有效 Wire 派生独立 JSON 是可设计的，但不能据此声称
已经存在经过验收的端到端 TLS 预检入口。

本轮没有用预检 JSON 污染 OpenSSL stdout/stderr，没有增加 Stream ID、额外 start 帧，
也没有通过修改 emit、替换类或改写生成函数绕过当前冻结边界。

## 4. 离线复现

本 Run 的 `offline_capability_probe.py` 直接调用现有 Encoder/Decoder：

- 合成 start 附带 `remote_preflight`，Decoder 能原样交付，证明底层表示能力存在。
- 使用纯内存 Mock Process 调用未修改的 `produce_session()`；start/exit 中没有预检记录。
- 检查函数签名及实际 start 调用，确认没有独立元数据入口。
- 没有启动 SSH、OpenSSL、远端命令或网络连接；合成记录不是真实远端证据。

结果见 `adapter_capability_audit.json`，探针检查通过，工程接线缺口仍在。
没有新增或修改仓库测试，不把探针结果写成真实 TLS PASS。

## 5. 本地 SSH 与 CA 预检

OpenSSH：`OpenSSH_9.6p1 Ubuntu-3ubuntu13.20, OpenSSL 3.0.13 30 Jan 2024`。
这是本地 SSH 所链接库的信息，不是远端 s_client 的版本观测。

本地 `ssh -G` 展开最终计划参数，未连接服务器：

| 项目 | 本地检查结果 |
| --- | --- |
| Alias / 目标 | risk-agent-remote / root@121.48.161.243:4035 |
| IdentityFile | /home/three/.ssh/risk_agent_audit，存在 |
| 公钥指纹 | SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A，与可信记录一致 |
| UserKnownHostsFile | /home/three/.ssh/known_hosts，存在 |
| StrictHostKeyChecking / BatchMode | 开启 |
| IdentitiesOnly | 开启 |
| PasswordAuthentication / KbdInteractiveAuthentication | 均关闭 |
| ForwardAgent / 所有端口转发 | 均关闭；ClearAllForwardings=yes |
| ControlMaster / ControlPath | false / none |
| ConnectionAttempts / ConnectTimeout | 1 / 15 秒 |
| SSH Overall Timeout | 计划 30 秒，未启动监督器 |

原 builder 默认 ConnectTimeout=10；本地仅构造当前任务授权的 15 秒计划并用 -G 核对，
没有修改 builder 或执行该连接。完整未执行 argv 在 `local_preflight.json`。
静态展开的诊断另存 `ssh_static_config.log.raw`，不冒充真实 `ssh_client.log.raw`。
未使用 -F /dev/null、Tunnel 或额外远端身份查询。

CA 来源：RISK_AGENT_CA_BUNDLE；路径
`/home/three/.config/risk-aware-smolagents/pki/ca.crt`，存在且可读。
SHA-256：`97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`，
与冻结 CA_SHA256 一致。没有替换、复制或输出 CA 内容。
本次实验不需要 API Key，没有读取或传输其明文。

## 6. 远端身份、Worker 与 OpenSSL 状态

本轮没有 SSH，以下均未重新观测：容器 hostname/uid/Ubuntu 身份、远端 OpenSSL
路径与版本、CA 可读性/哈希、Worker PID/完整命令身份/所有权、Listener 和进程存活。
`remote_preflight.json` 明确 status=NOT_EXECUTED，当前观测字段为 null。

历史参考：容器 36f825bcc5da、uid=0、Worker 0 PID=382420、start_ticks=519951147、
127.0.0.1:8000 监听、OpenSSL 3.0.2、CA 路径 /root/.config/risk-agent/tls/ca.crt。
这些仅为历史门禁基准，不是本轮重新核验结果。没有据此宣布 Worker 当前健康。

## 7. 冻结 TLS 参数与预算

从当前 remote_entry 的实际 AST 提取的命令，与历史命令一致；未执行：

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost
  -verify_hostname localhost -verify_return_error
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

冻结预算仍为 5 秒总 OpenSSL 进程预算，包含启动/TCP/TLS；没有改成额外 5 秒等待。
SSH 计划 Connect=15、Overall=30、Attempts=1；实际自动重试/Failover=0。
没有额外 openssl version、s_client -help、OpenSSL 握手或 TCP 健康探测调用。

## 8. TLS 判据及本轮结果

分类器保持原样：明确 SSL_connect 完成标记，或完整 brief 建连摘要
（CONNECTION ESTABLISHED、有效 TLS 协议、非空且非 NULL 的密码套件），才能设置 complete；
最终成功还要求验证 OK、退出 0、未超时且无 fatal alert。单独 Verification: OK、
Finished 状态或退出 0 均不足以证明握手完成。

本轮没有真实输入，**没有调用分类器生成一个伪造的实验分类**。

| 字段 | 本轮值 |
| --- | --- |
| tls_handshake_started | false |
| tls_handshake_completed | null |
| certificate_chain_verified | null |
| hostname_verified | null |
| ClientHello / ServerHello / Certificate / CertificateVerify / Finished | null（未观测） |
| TLS Alert / 握手超时 | null（未观测） |
| openssl_exit_status | null |
| tls_classifier_result | null，NOT_RUN_NO_REAL_TLS_INPUT |
| tls_outcome | NOT_ATTEMPTED |

本轮没有真实握手阶段时间线，不补造 Finished、成功摘要或证书拒绝。

## 9. 原始证据与退出状态

| 真实证据类型 | 字节数 / SHA-256 | 状态 |
| --- | --- | --- |
| OpenSSL stdout / stderr | null / null | 未产生 |
| SSH Client Log | null / null | 未产生连接日志；静态 -G 日志单独标识 |
| SSH Wire / 本地进程 stderr | null / null | 未产生传输证据 |
| Remote Exit Receipt | null / null | 未产生，不伪造退出码 |
| 统一采集观察时间线 | 无真实采集事件 | 未创建伪造 Trace |

SSH 返回码、Remote Exit Status、OpenSSL Exit Status 均为 null；
remote_exit_status_observed=false，remote_termination_status=UNKNOWN。
没有因为“未执行”就填入成功 0、失败 23 或已回收 true。

所需真实 raw 文件没有用空文件占位。`transport_verification.json` 列出未产生文件及
NOT_EXECUTED 语义。当前 Manifest 证明本次本地审计文件完整，不证明已经有 TLS 原始证据。

## 10. 产物与 Manifest

目录：`runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_002/`。

保存 run_manifest、baseline、local_preflight、adapter_capability_audit、离线探针与防护入口、
测试结果、remote_preflight、process_lifecycle、exit_status_audit、tls_classification、
transport_verification、resource_release、gate_summary、完整性检查及本报告副本。
所有文件属于本次 Run；静态检查、内存合成与未执行状态均标明来源。

统一 artifact_manifest.json 使用当前本地审计类型、Run ID、相对路径、大小、SHA-256、
ctime 观察值和归属说明。ctime 不冒充文件 birth time。全部文件 fsync 后先写独占 pending，
再以 no-clobber 硬链接原子发布，重新读取逐项核对，并检查文件集合覆盖；清单排除自身。

基线覆盖 229 个历史文件、18 份历史 Manifest、177 个受保护源码/测试/配置文件及
原有未跟踪文件。完成后再次匹配；历史 SSH 002 的两级 Manifest 保持原样。
早期 worker0 诊断清单只记录哈希而无 size_bytes：按原有 SHA 校验，不补造历史大小。

## 11. 测试、异常与扫描

本轮 full suite 在禁止网络/真实 SSH/OpenSSL 的 audit hook 下运行，覆盖 TLS Evidence
Capture、SSH Transport/Compatibility、TLS Classifier、Remote-Local Isolation 及其他回归。

- 全仓：**577 passed in 43.77s；0 failed；0 skipped**。
- 独立内存能力探针：PASS，无真实子进程或网络。
- compileall：src、scripts、tests 及本次离线探针通过。
- git diff --check：通过。
- 历史 Manifest、文件 SHA、冻结配置及源码前后核对：通过。
- 新产物及日报敏感材料扫描：通过；未输出、传输或保存 API Key/私钥。

防护测试中的 urllib3 导入期 IPv6 bind 能力探测被阻止；没有额外网络连接。
真实 SSH/TLS 异常链不存在，因为未启动连接。不是遗漏了真实 SSH stderr。

本地最初的历史清单检查假设每项都有 size_bytes，遇到旧哈希清单产生 KeyError，
当时尚未创建本 Run，也没有启动 SSH。修正本地核验脚本以兼容历史清单字段，完整
复核后通过；该本地问题及处置另存 local_preflight_issue.json，不修改历史 Manifest。

```text
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_002/offline_capability_probe.py
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_002/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_002/offline_capability_probe.py
git diff --check
```

## 12. 资源释放与副作用

没有创建连接型 SSH、OpenSSL、Tunnel、远端文件、Listener 或服务资源，因而没有本次
真实连接进程需要 terminate/wait。resource_release.json 明确零创建和不适用字段。
没有向既有 PID 发送信号，没有关闭 ControlMaster，没有访问 Worker 1/GPU/Docker。
没有远端命令，故未改变 Worker；其当前存活状态也没有被测量，不声称已有新证明。
本地全仓回归中的 Fake/Mock 与既有生命周期测试均不是远端实验。

## 13. 后续前置条件

当前不具备进入 Connection Smoke Test 的条件；TLS、证书链、hostname 和完整原始输出
均未获得本轮验收。不得启动 HTTP、Qwen、五任务 Gate、P2-D3 或正式采集。

下一项应先独立审核并授权离线实现 TLS bootstrap 与预检元数据接线：复用 V1 既有
控制结构、保持两条 OpenSSL Payload 纯净，增加真实生成端至接收端的 Fake 回归，
覆盖预检失败不启动 TLS、单 OpenSSL 调用约束、退出收据和独立 JSON 持久化。
这不是请求新增 Stream ID 或降低判据；本轮没有实施这些变更。
完成该离线前置并验收后，才讨论新的唯一 Run 和一次真实 TLS 授权；不复用本次目录。

## 14. 最终状态

**TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**，原因是连接前的生产者/预检接线前置条件缺失。
真实 SSH/TLS 均 0，未消耗真实连接额度；TLS 是否成功仍未知。
本地审计 Manifest 匹配，历史证据和冻结文件不变，未 Commit/Push。
本轮停止，等待用户审核，不自动进入后续阶段。
