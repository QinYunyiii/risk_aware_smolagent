# P2-D2 SSH Evidence Transport Adapter — Stage A

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。最终 Gate：**SSH_EVIDENCE_TRANSPORT_STAGE_A_PASS**。

新增独立SSH Evidence Transport Adapter、离线入口及53项测试，复用TLS Evidence
Capture的字节持久化、观察时间线、Manifest和本次句柄生命周期管理。
全仓最终 **531 passed in 40.61s，0 failed，0 skipped**；compileall、diff检查和
敏感信息扫描通过。54份历史文件及六份Manifest保持不变。

**本轮没有真实SSH传输证据。Fake SSH测试不能替代真实OpenSSH远端验证。**
只执行本地ssh -V、ssh -G的配置/usage/参数检查；没有SSH连接、Remote Command、
OpenSSL、TLS握手、HTTP、Qwen、Docker、vLLM或GPU操作。停止于Stage A。

## 2. 既有阻塞与实现审计

此前Evidence Capture Stage B因SSH无损接线缺失而在连接前BLOCKED，原结论不变。
本次审计确认：旧remote_local_tls_isolation仍按行decode、筛选summary，未用-E；
其原始PIPE虽是binary，保存的JSON不能还原原始字节。现有SSHProcess使用argv数组、
独立前台句柄和身份校验；生产转发路径有-F /dev/null，本次不复用该转发配置。
没有发现需要修改TLS分类器或冻结协议的理由。

原EvidenceStore只能保存两个.bin流，capture_process默认5秒预算。为避免复制：
仅扩展其可配置stream→filename映射、外部生产的-E日志观察、启动前Manifest预留、
流式解码/结束/最终审计回调及显式外层进程超时；原默认stdout/stderr和5秒行为保留。
原53项Evidence Capture测试全部通过，历史Manifest仍可按旧schema读取。

## 3. 文件与架构

- 新模块：src/risk_agent/collection/ssh_evidence_transport.py。
- 集成扩展：src/risk_agent/collection/tls_evidence_capture.py。
- 新入口：scripts/test_p2_d2_ssh_evidence_transport.py。
- 新测试：tests/test_ssh_evidence_transport.py。
- 本报告。

capture_ssh_transport只调用一次显式注入的process_factory；没有默认真实启动器。
未来受审的工厂负责创建binary、shell=False、独立会话、无凭据环境的SSH句柄，并在
交付句柄前失败时自行回收半初始化子进程。适配器包装该句柄并复用capture_process。

传输层保留SSH原始两条pipe和-E日志；FrameDecoder从已持久化的wire数据中恢复
明确归属的远端两流。接收端不改变TLS输出内容，不借字符串猜测流来源。
原TLS分类器和旧运行入口均未修改。

## 4. 三类输出与未分类stderr

| 文件 | 权威内容/来源 |
| --- | --- |
| ssh_client.log.raw | -E直接写出的客户端日志；只观察和fsync，不重写/过滤 |
| remote_stdout.raw | 经逐帧长度、offset、SHA及终结校验恢复的远端stdout bytes |
| remote_stderr.raw | 同协议明确标识的远端stderr bytes |
| ssh_stdout_transport.raw | SSH stdout完整wire bytes，含帧/收据；不是直接冒充应用stdout |
| ssh_stderr_unclassified.raw | SSH stderr原始bytes，可能含本地错误或远端bootstrap错误 |

远端逻辑stdout/stderr通过SSH stdout中的带来源帧传送，remote_stderr.raw不从本地
SSH stderr猜测提取。数据帧封装不改变内层原始bytes；不会把frame/control信息写入
remote_stdout.raw或remote_stderr.raw。原始wire另存以便独立复核。

**不能因为用了-E就声称SSH stderr完全属于远端。**未分类流始终保留，结构化字段
local_stderr_provenance=UNCLASSIFIED_SSH_OR_REMOTE_BOOTSTRAP；不会按含error或
Permission denied等词语强行归因。本地客户端-E文件也不能证明所有本地错误均被
重定向，尤其bootstrap/参数解析早期错误。

所有raw保持bytes，不解码、不删除空行、不改CRLF、不做字符串替换。raw目录0700、
文件0600，Manifest禁止自动发布；结构化审计只记录安全字段、大小、哈希及状态，
不把payload或任意异常原文复制到普通日志。实际密钥不进入argv。

## 5. OpenSSH -E真实本地检查

本机实际版本：OpenSSH_9.6p1 Ubuntu-3ubuntu13.20，链接OpenSSL 3.0.13。
证据保存在本次独立审计目录：

1. ssh -V原始stdout/stderr。
2. ssh -G -?仅触发本地usage，返回255；usage明确列出-E log_file。
   这是本地帮助返回码，不是SSH传输失败或远端退出码。
3. 本地/usr/share/man/man1/ssh.1.gz说明-E将debug日志追加到log_file。
4. 按最终argv加-G，使用独占预建的local_openssh_E_config.log.raw，返回0，实际生成
   配置读取诊断日志；完整配置输出和日志保持原样。

参数接受性及本地debug重定向由真实OpenSSH确认，未用Fake行为代替。
**尚未确认真实远端会话中所有stderr/异常的分流行为**，该限制保留至Stage B。
没有为检查-E而建立真实连接。

## 6. SSH argv和安全配置

ssh_argv使用绝对executable path和argv数组；无shell=True，无任意shell命令拼接。
固定Alias risk-agent-remote，端口4035；专用IdentityFile和UserKnownHostsFile为原已验证
路径。以下通过本地-G核验：

- BatchMode=yes、StrictHostKeyChecking=yes、IdentitiesOnly=yes。
- PasswordAuthentication=no、KbdInteractiveAuthentication=no、ForwardAgent=no。
- ClearAllForwardings=yes、ConnectionAttempts=1、ConnectTimeout=10。
- ControlMaster=no、ControlPath=none；无-L/-R/-D，没有历史转发遗留。
- 不使用-F /dev/null，不绕过已验证Alias/known_hosts配置。
- -E指向本次独占Run内唯一文件，使用-v；Remote Command固定为python3 -B -。

本轮只构造这一Remote Command，没有运行。入口只允许offline_preflight，显式显示
network_enabled=false、ssh_enabled=false、remote_command_enabled=false、
real_qwen_enabled=false；fake_ssh_executable=null表示默认未启动任何测试进程。
没有--execute开关；Fake脚本由测试显式注入，真实连接必须另获Stage B授权。

## 7. 远端帧协议及退出语义

协议risk-ssh-evidence-v1：每帧有固定前缀、Run ID、本次nonce、严格递增sequence。
start→data*→exit；数据帧包含stream、offset、byte_count、base64 payload、chunk SHA、
远端UTC及monotonic_ns。单帧上限128 KiB，适配器按块解码，未完成帧缓冲有界。
原始wire先落盘，再解码和持久化内层流；截断/错序/跨Run或nonce/非法base64/长度或
哈希不符/额外尾帧/缺终结收据均失败关闭。

exit收据必须有明确returncode、reaped=true、timed_out和两流最终大小/SHA，且与所有
已收到数据相符。nonce绑定防止意外复用旧Run帧，不代替SSH主机认证，也不声称提供
独立于SSH的加密认证。任意不受信任程序伪造帧不属于可信生产者；Stage B必须固定
并审核实际发送的wrapper源码。

remote_wrapper_source可生成stdlib-only生产者源码，复用现有PopenPipeProcess读取
机制，不在远端创建目录/文件。produce_session将两条pipe无损封装，保持独立5秒
OpenSSL总预算及自己的所有权/回收收据。源码仅定义authorized_launch_after_preflight，
不自动调用；本轮只编译/加载定义并用Mock生产者测试，没有执行远端包装器。

| 字段 | 严格语义 |
| --- | --- |
| ssh_process_return_code | 本地SSH句柄wait返回值 |
| remote_exit_status | 有效终结收据中受包装命令的返回码；无收据则null |
| remote_exit_status_observed | 是否取得且完整验证该收据 |
| ssh_transport_status | COMPLETED_WITH_REMOTE_RECEIPT、SSH_255_AMBIGUOUS、LOCAL_TIMEOUT或FAILED_OR_INCOMPLETE |
| local_timeout | 本地监督期限触发，与远端TLS预算独立 |
| remote_termination_status | 有可靠收据才EXITED/SIGNALED，否则UNKNOWN |
| remote_timed_out | 仅取远端收据，不以本地超时代替 |

SSH返回255不会自动当成远端255；有明确远端255收据时可以独立记录远端255，但SSH
传输层仍保留255歧义。SSH返回0而无收据也不补远端0。debug里的Exit status字符串不
代替终结收据。remote_exit_status指受包装进程，不是凭SSH返回值推导出的Python控制
程序状态；控制程序本身没有独立收据的状态不额外推断。

## 8. 进程生命周期、超时与清理

SSHLogProcess将预建-E文件按原始块tail，stdout/stderr用既有selector并发读取。
日志EOF只有SSH已退出、pipe已排空且日志已读完时产生；日志缩短即失败，不把轮询
时暂时没有新字节当最终EOF。不先等进程退出再读PIPE，Fake大输出已超过pipe容量。

通用capture_process保存argv、实际executable path、PID/start identity、提交/接收/
退出观察/结束UTC和monotonic耗时、returncode、signal、timeout、terminate/kill/reap
以及五种流的实际文件大小、已接收和已持久化计数。时间为观察值，不冒充精确exec时间。

SSH本地外部期限默认40秒，不是TLS预算；原TLS采集默认5秒不变，远端包装进程也
保持5秒。超时先fsync已接收证据，再核验本次句柄身份、terminate、有限宽限期、
必要时再次核验后kill、wait/reap。debug-tail初始化失败也先交付本次句柄给共享清理
控制器，避免初始化错误丢失所有权。不会清理其他SSH或历史ControlMaster。

**本地SSH被回收不证明远端命令已停止。**没有有效远端回收收据时状态UNKNOWN；
不追加SSH、不执行pkill。远端producer自己的5秒watchdog独立运行；链路已断且没有
终结帧时，不能仅凭“设计有watchdog”声称实际远端回收已完成。

## 9. Artifact Manifest与失败关闭

复用EvidenceStore安全目录、O_EXCL/O_NOFOLLOW、流式write/fsync、chunk时间线、
SHA-256以及pending→no-clobber hard link原子发布。没有另写一套raw Writer或Manifest。
预先准备五种raw、核心生命周期、remote_observed_timeline.jsonl、transport_status.json
并预留/fsync Manifest.pending，capture_prepared成功后才调用一次工厂。任何准备错误
的测试均证明SSH启动次数为0。

统一Manifest涵盖全部上述实际文件，排除自身。verify_manifest支持旧默认两流和新
stream_files映射，逐项验证大小、文件SHA、chunk SHA、offset、累计数与时钟域。
已有文件不会被覆盖；最终写入失败不发布半个Manifest，保留部分证据。
verified_remote_evidence要求Manifest及完整采集/远端收据验证通过，才允许交给独立
分类器。传输完成、证据完整与TLS成功是不同结论。

combined_observed_timeline保留sequence、monotonic_ns、utc_timestamp、stream、
byte_count、cumulative_bytes、chunk_sha256及源文件offset。顺序只表示本地观察读取
顺序；remote_observed_timeline另存远端读取观察时间与本地接收时间，不能跨主机
单调时钟相减，亦不保证子进程真实write顺序。

默认8 MiB安全预算计算wire、还原两流、stderr及日志的总观测量，不静默截断。
超限显式StorageLimitExceeded、失败收据和实际收到/已存大小，回收本次句柄。
-E由外部进程写文件，因此本地检测到超限前可能已写出更多字节；这不是硬磁盘配额。
超限时保留实际已有文件及其大小，绝不将超限输出宣称完整PASS。

## 10. 离线测试与覆盖

- 首轮适配器42项＋原采集53项：95 passed。
- 随后专项扩展：52 passed；再补1项debug-tail初始化失败测试。
- 最终新增53项及全部既有测试：**531 passed in 40.61s，0 failed，0 skipped**。
- 原TLS Capture、Classifier、Isolation、Worker 0诊断和连接相关回归均包含在全仓运行中。
- compileall覆盖src/scripts/tests，通过；git diff --check通过。新增文件另用no-index
  --check验证无空白诊断（新增差异返回1并非测试失败）。

覆盖：安全argv及唯一-E、三种raw逐字节/哈希/大小一致、空流、非UTF-8、跨块UTF-8、
所有wire切分边界、大规模并发PIPE输出、正常/非零/远端255/SSH255、缺收据、明确
远端signal/timeout、断流、校验错误、本地超时和部分输出、terminate/kill/reap、
Manifest/fsync/中途审计失败、预启动失败零启动、路径穿越、超限、敏感数据审计、
并发Run、日志初始化失败及控制源代码不自动启动。历史fixture相关测试仍判不确定。

Fake SSH用本地Python脚本同时写两个PIPE和-E文件，使用无凭据环境；没有调用真实
SSH可执行文件。Mock测试注入句柄/时钟，没有启动进程。Fake模拟行为只证明适配器
对该输入正确，不能证明真实OpenSSH远端行为与之完全一致。

完整命令：

```text
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_transport_stage_a/p2_d2_ssh_evidence_transport_stage_a_20261125_001/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python scripts/test_p2_d2_ssh_evidence_transport.py
git diff --check
```

全仓运行加了Python audit hook禁止连接/解析/发送/绑定和真实SSH/OpenSSL等工具启动。
仅记录1次被阻止的urllib3导入期本地IPv6 bind能力探测，未发起连接或发送。
本地-V/G兼容检查在该禁止启动guard之外单独执行，均不建立连接。
敏感信息扫描在内存中比较实际继承凭据是否进入修改文件，结果未命中，不记录值或
其摘要；私钥/证书材料未发现。测试中的secret为明确合成样例。

## 11. 可检查产物与历史不变性

独立审计目录：
`runs/phase2_p2_d2_ssh_evidence_transport_stage_a/p2_d2_ssh_evidence_transport_stage_a_20261125_001/`。

保存baseline、旧采集源码副本与集成diff、本地OpenSSH版本/usage/G/E原始输出、
兼容结论、全仓测试结果、防护入口、完整性检查和Manifest。
synthetic_adapter_sample/展示五种raw、两个观察时间线、SSH生命周期、退出语义及
统一Manifest。synthetic_sample_receipt.json明确无真实连接/命令，远端返回7只是
fixture值，不是一次真实命令退出。

54份历史文件、六份Manifest及五份受保护的分类器/旧入口/fixture/冻结配置哈希匹配。
只允许本任务需要的tls_evidence_capture.py集成扩展；其他已有未提交文件均保持原样。
旧Stage B仍BLOCKED，历史Isolation仍DIAGNOSTIC_INCONCLUSIVE，不事后改判。

## 12. Git状态与资源

HEAD保持bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。既有未跟踪文件保留；本轮修改
一个集成模块，新增适配器、入口、测试和本报告。审计/合成raw均在Git忽略的runs目录。
未暂存、Commit、Push、fetch，未修改CA、SSH配置、TLS分类器、模型参数或研究协议。

真实SSH连接=0、Remote Command=0、Tunnel=0、OpenSSL/TLS=0、HTTP=0、Qwen=0；
Docker/vLLM/GPU/远端文件及进程管理均为0。仅本地Fake/Python测试进程，均由本次
句柄回收；没有触及其他用户进程，没有新建远端目录或写入远端文件。

## 13. Stage B前置条件与停止

本轮交付的是可离线验收的传输接收器、生产者协议/模板及生命周期接线，不是一次
真实SSH验收。后续须另获明确Stage B授权，并在新的唯一Run中：

1. 再核验本地身份配置、-E、输出空间和启动前持久化；固定实际SSH/Python路径与
   wrapper源码哈希，提供经过审查的唯一真实工厂。
2. 将身份、容器、CA SHA、Worker所有权/模型参数和Listener门禁放在
   authorized_launch_after_preflight调用之前；泛用传输模块不能代替这些TLS门禁。
3. 仅一次SSH Remote Command、一次Worker 0原参数5秒握手，验证真实-E日志行为及
   未分类stderr保留，不访问Worker 1、不发送HTTP/Qwen、不改Worker。
4. 完整校验raw/终结收据/Manifest后才运行冻结分类器；缺证据、断链或失败立即停止，
   不补远端0、不自动重试，不因本地回收成功而推断远端已回收。

本轮不调用生成源码的launch函数，不进入上述真实流程、Smoke、完整Gate或P2-D3。
最终 **SSH_EVIDENCE_TRANSPORT_STAGE_A_PASS**。停止等待审核。
