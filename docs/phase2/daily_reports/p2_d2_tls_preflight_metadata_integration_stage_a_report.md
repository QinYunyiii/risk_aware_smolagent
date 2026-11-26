# P2-D2 TLS Preflight Metadata Integration — Stage A

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。最终 Gate：**TLS_PREFLIGHT_METADATA_STAGE_A_PASS**。

已将独立 TLS 预检记录接入生产者、V1 start 扩展、权威 Decoder、独立持久化和 Manifest。
新增 **69 项专项测试**；最终全仓 **646 passed in 41.91s，0 failed，0 skipped**。
已保存使用实际序列化生产者的本地 Fake SSH 样例：元数据精确匹配，stdout 55 bytes、
stderr 14 bytes、退出收据 23 保持不变。没有执行真实 SSH、OpenSSL、TLS、HTTP 或 Qwen。

本轮解决的是上一轮连接前的元数据接线缺口；历史 TLS Stage B 002 仍为 BLOCKED，
不事后改判。Stage A PASS 不是一次真实 TLS 验收，也不授权自动进入 Stage B。

## 2. 原因与实际调用链

修改前：FrameEncoder.start 可通过现有 **values 编码附加字段，FrameDecoder 可交给 sink，
但 produce_session 只发送时间戳，远端模板没有预检参数，接收端没有独立元数据文件。
上次固定 Payload 的真实 SSH PASS 没有覆盖这条预检接线。

当前调用链：

```text
已生成的 TLS Preflight 记录
  → preflight_metadata_bytes：字段、关联、安全检查和规范化
  → produce_session(..., preflight_metadata=..., process_factory=...)
  → 既有 FrameEncoder.start / encode_frame 的可选扩展字段
  → SSH stdout 的 V1 Wire（本轮为本地 Fake）
  → FrameDecoder：独立元数据哈希、Run / Session / Worker 检查
  → capture_ssh_transport：remote_preflight.json + fsync + Manifest
  → verified_preflight_metadata：文件、清单与原始 Wire 再核对
```

元数据生成器负责提供真实的远端观测；本模块不把期望记录当成远端事实，不执行
远端探测，也不根据本地静态输入宣称远端门禁已经通过。

## 3. 修改文件与生产者生命周期

修改：`src/risk_agent/collection/ssh_evidence_transport.py`。
新增：`tests/test_tls_preflight_metadata.py`、本报告；审计文件只新增到独立 Run。
tls_evidence_capture.py、TLS Classifier、旧测试、fixture 和冻结配置没有修改。

produce_session 增加可选 preflight_metadata 和惰性 process_factory：

1. 验证并规范化预检记录，建立 Run / Session 绑定。
2. 使用原有 start 帧发送独立元数据及其 SHA-256。
3. 预检 FAIL 或发送异常时，不调用受监督任务工厂，不伪造退出收据。
4. PASS 且 start 成功交付后，才调用工厂；维持原 5 秒受监督进程预算及所有权回收。
5. 后续 stdout/stderr、exit 帧和真实 wait 收据仍由原有实现处理。

元数据模式拒绝传入已经启动的进程，避免“任务先执行、元数据后到”的调用顺序。
旧版 process 参数调用继续可用。远端模板的 authorized_launch_after_preflight 增加
同一可选参数，直接调用这一生产者，不替换 Encoder、不另写第二套帧协议。
模板仍不自动启动；真实远端身份/CA/Worker 门禁必须在未来授权的 bootstrap 中执行。

## 4. Schema 映射与协议边界

审计发现此前没有独立的完整 TLS Preflight 应用记录 Schema，只有 V1 通用扩展能力。
本轮使用任务指定名称建立应用层校验；没有增加帧类型、Stream ID 或修改必需帧字段。
所有字段仍位于现有 start 的开放扩展位置 remote_preflight 中。

| 应用记录字段 | 映射及校验 |
| --- | --- |
| run_id | 必须等于外层 V1 run_id |
| session_id | 映射到既有 nonce，必须完全一致 |
| worker_id | 当前独立诊断限 worker-0 |
| remote_container_id | 12–64 位小写十六进制容器标识 |
| remote_hostname | 必须与容器标识一致 |
| remote_user | 当前授权目标 root |
| endpoint | 当前授权目标 127.0.0.1:8000 |
| openssl_version | OpenSSL 3.x 版本字符串；与本地期望对照 |
| openssl_executable | 无路径跳转的绝对路径，basename=openssl |
| ca_path | 绝对路径，禁止路径跳转、控制字符及认证信息 |
| ca_sha256 | 小写 64 位十六进制；与本地可信期望对照 |
| tls_parameter_fingerprint | 小写 64 位十六进制；由调用方生成，与本地冻结参数期望对照 |
| connect_timeout | 数值 15；拒绝 bool、NaN 和其他预算 |
| handshake_timeout | 数值 5；保持冻结预算 |
| preflight_timestamp | 带 UTC 时区的 ISO 时间；保留远端生成值 |
| preflight_status | PASS / FAIL；FAIL 不启动受监督任务 |

拒绝未知字段及凭据形式，异常不输出字段值；不记录 API Key、私钥或证书内容。
规范化为排序键、紧凑 ASCII JSON，超时数值统一为整数。传输中的
remote_preflight_sha256 是规范化 JSON 的哈希；独立 .json 文件增加结尾 LF，
其完整文件哈希另由 Manifest 记录，二者不混淆。

remote_preflight 与 remote_preflight_sha256 是 start 的可选应用扩展；旧 V1 start
不要求这些字段。权威 encode_frame、data_frame、exit_frame、FrameEncoder、ssh_argv
和 exit_semantics 的 AST 与修改前完全一致；PROTOCOL、PREFIX、帧大小上限、
数据 stream 名、data/exit 必需字段均不变。

## 5. 接收、独立持久化与再验证

capture_ssh_transport 增加可选 preflight_metadata 和 require_preflight_metadata。
接收前先规范化本地期望，保存 preflight_expectation.json，并预留 remote_preflight.json。
本地期望明确标为 LOCAL_EXPECTATION_NOT_REMOTE_OBSERVATION，不能当成实测结果。
所有必要创建、写入、fsync、目录同步及 prepared 审计均在 SSH 工厂之前完成。

Decoder 从 start 交付验证后的独立记录。接收端先保存收到的记录并 fsync，再对照
本地期望；时间戳来自远端、状态可能拒绝，其余字段必须匹配。
身份或 CA 等不匹配、FAIL、哈希不符、缺少必需元数据均禁止完整成功采集。
能保存的失败记录、原始 Wire 和局部输出保留，缺少 exit 时不补造状态。

两个独立元数据文件纳入原 EvidenceStore 的 Manifest；未修改通用采集模块。
verified_preflight_metadata 要求它们实际列入 Manifest，重算文件哈希，再从原始 Wire
通过权威 Decoder 重建记录，精确比较独立文件。仅有文件存在或重新改写 Manifest
都不足以绕过 Wire 一致性检查。verified_remote_evidence 在元数据模式调用该验证。

## 6. 隔离性与 fail-closed 口径

元数据不进入 OpenSSL 两流、SSH Client Log、Agent Memory 或 Checkpoint。
没有调用 Agent/Checkpoint 模块。Wire 中控制扩展与两条应用 Payload 分开记录，
退出收据保持独立，不把元数据当成 stdout 或退出状态。

| 失败时点 | 经过测试的行为 |
| --- | --- |
| 本地字段缺失、绑定错误、序列化/安全校验失败、预检 FAIL | SSH 工厂调用 0 |
| 本地元数据文件创建、准备写入、fsync、prepared 审计失败 | SSH 工厂调用 0 |
| 生产者 start 发送失败或预检 FAIL | 受监督任务工厂调用 0 |
| 接收后元数据写入/fsync 失败、哈希或身份不符 | 回收本次已交付句柄，禁止成功；保留可写出的证据 |
| exit 收据缺失 | 已收元数据保留；remote exit 未观测；禁止交付成功证据 |

必须区分启动前与接收期间。接收真实远端记录时，SSH 已经存在；之后磁盘失败不能
倒写为“SSH 启动 0”。本轮不声称仅凭 os.write 完成就取得接收端磁盘提交确认，
也没有新增双向 ACK 协议。启动前零调用保证与接收后受控回收分别验收。

## 7. 新增测试与本地 Fake 端到端证据

新增 69 项测试覆盖：完整记录/规范化、每个必需字段缺失、Run/Session/Worker/Endpoint
关联、超时/时间/路径/版本校验、未知敏感字段、不可序列化值、Metadata 哈希错误、
创建/写入/fsync/审计失败、发送失败、预检 FAIL、多 Session 与并发隔离、旧 V1、
Payload/Client Log/exit 隔离、篡改及清单漏项、退出收据缺失和冻结分类器输入一致性。

真正的本地 Fake SSH 测试使用 remote_wrapper_source 生成生产者，启动本地 Python
Fake Client，再由该生产者监督一个本地 Python 字节子进程。完整经过原 Adapter、
权威 Encoder/Decoder、独立 JSON、真实本地 wait 收据及 Manifest，不只是编解码单测。
该 Fake 不执行 OpenSSL，不连接网络；fixture 的远端身份字段是合成样例。

可检查样例：本 Run 的 `local_fake_example/metadata_case/`。

| 文件 | bytes | SHA-256 |
| --- | --- | --- |
| remote_preflight.json | 628 | aa00eea77dc1325ef3429eb1c5067ec2102de7ecba4deb5f1473e8efe023485f |
| remote_stdout.raw | 55 | cb30b9a81ad88376035fbd5466b8243d9ba4ae628a2525c361bf4c6f64b98630 |
| remote_stderr.raw | 14 | 580172f071a944333f1fa16b45aa79564d5d82f899568fcf8cad46ddbb58c804 |
| ssh_client.log.raw | 23 | 9df25e09e136d3707fc8ae9b2748e269de0e8ef358b1a74c078ab876c5d0b6e6 |
| ssh_stdout_transport.raw | 3064 | 1ad311b21612cb24e92dcd3eadb1783dba347dbceeed12fee26a31d51892b7f7 |

此样例退出码 23，Transport=SUCCESS，Command=COMPLETED_NONZERO；收据有实际本地
子进程 PID/start identity/reaped。不是远端真实 SSH/TLS 证据或风险训练数据。

## 8. 测试结果及开发记录

- 新专项：69 项，最终全量中全部通过。
- SSH 原有 99 项及其他相关连接回归全部通过。
- 中间专项集合：331 passed in 9.13s；随后新增的 3 项 Manifest/缺收据测试纳入最终全仓。
- 最终全仓：**646 passed in 41.91s，0 failed，0 skipped**。
- compileall（src/scripts/tests）及 git diff --check：通过。
- 未跟踪源码另与修改前副本形成 implementation_change.patch，避免 git diff 漏掉变更。
- 敏感材料扫描和历史 Manifest/冻结文件校验通过。

首轮开发测试 18 failed / 140 passed：一处过宽的 sk- 检查误匹配 risk-agent 公共路径，
另一本地 Fake 的多行 -c 参数被原有 argv 安全检查拒绝。修正为词边界及单行合成命令后
158 通过；没有放松 argv 校验。首轮失败记录保留。第一轮全仓为 643 通过，之后增加
Manifest 漏项拒绝及缺失 exit 保留证据检查，最终为上述 646。
可检查样例驱动首次缺少仓库根导入路径，启动子进程前失败；补齐该 Run 专用驱动的
sys.path 后通过，修复前驱动及异常记录保留。没有任何真实连接重试。

最终命令：

```text
.venv/bin/python runs/phase2_p2_d2_tls_preflight_metadata_stage_a/p2_d2_tls_preflight_metadata_stage_a_20261125_071011/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_tls_preflight_metadata_stage_a/p2_d2_tls_preflight_metadata_stage_a_20261125_071011/offline_example.py
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

测试防护在系统调用前拒绝网络及真实 SSH/OpenSSL 工具。urllib3 导入期的 IPv6 bind
能力检查被阻止并留痕，不是一次已建立的连接。Fake 子进程为已审查的本地 Python 字节程序。

## 9. 历史兼容性与证据保护

独立 Run：
`runs/phase2_p2_d2_tls_preflight_metadata_stage_a/p2_d2_tls_preflight_metadata_stage_a_20261125_071011/`。

修改前保存 HEAD/Git 状态、源文件、原有未跟踪文件、250 个历史文件及 19 份 Manifest。
结束时历史哈希全部匹配；源码基线中仅授权的 ssh_evidence_transport.py 改变。
此前 TLS Stage B 002 的 **21 项清单逐项完整匹配**，历史 SSH Stage B 的两级 Manifest
也保持不变。旧 SSH/TLS fixture 未改写，原历史分类和 Gate 不变。
Checkpoint v2、B2/B3/B4 协议、1024-token 配置、CA、TLS 参数、分类器均未修改。

## 10. Git、安全与资源统计

HEAD 保持 `bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支 main。
保护用户所有既有未提交文件；没有 Commit/Push/fetch 或 GitHub 请求。
只对授权模块做集成变更，新增专项测试、报告和本地审计；没有远端文件写入。
本次目录使用私有权限，Fake 原始数据不自动发布；Manifest 原子发布后逐项重读验证。

真实 SSH=0、Tunnel=0、Remote Command=0、OpenSSL=0、TLS=0、HTTP=0、Qwen=0、
GPU/vLLM/Docker 操作=0。只运行本地 Mock/Fake/回归；样例进程自然退出并已 wait/reap。

## 11. Stage B 前置条件与停止

已解除本次审计确认的“Session Producer 无独立 Metadata 入口”阻塞，完整离线传输
路径及原始两流、元数据、退出收据和 Manifest 已一起验收。

下一次真实 Stage B 仍需独立授权、新唯一 Run；必须在唯一远端命令内实际核验容器、
用户、CA 哈希、OpenSSL 和 Worker 0 所有权/监听，再把实测记录传入已验收入口。
本地期望须先持久化，metadata 模式启用 require_preflight_metadata=True；不能以本轮
合成记录替代真实预检。未来的控制程序不需要改写 Encoder/Decoder 或重新设计协议。
本轮没有执行这些远端动作，也没有验证当前模型服务/TLS 健康。

最终 **TLS_PREFLIGHT_METADATA_STAGE_A_PASS**。本轮结束，不自动 SSH/TLS、Smoke、
完整五任务 Gate 或正式采集；未 Commit/Push，停止等待独立审核和授权。
