# P2-D2 Remote-Local TLS Isolation Test — Stage A

## 1. 执行摘要与最终 Gate

`REMOTE_TLS_ISOLATION_STAGE_A_BLOCKED`

独立实现、默认离线入口及22个定向测试已完成。严格TLS验证、无转发SSH命令、固定
5秒外部预算和fail-closed分类均已验证到离线层。但尚无**实际远端OpenSSL版本与
s_client参数支持证据**。按本任务“实际远端版本兼容性检查”的严格验收条件，不能
用WSL本机版本或Mock结果代替它，因此不宣告Stage A PASS。

本轮没有SSH连接、远端命令、真实TLS握手或实验Run。没有修改现有连接管理器。
后续即使获得授权，也必须先通过同一远端命令内的身份/版本/参数/CA门禁，再允许
唯一一次s_client连接；未知、不兼容或缺失均停止，不重试。

## 2. 历史TLS失败证据完整性

已读取TLS Forensic Audit和Worker 0诊断报告，核验两个原始Run：

| Run | Manifest SHA-256 | 产物核验 |
| --- | --- | --- |
| p2_d2_connection_smoke_20261125_001 | af3ae0b5dd8d0f2f9fbec9518ffeb43e85663e2e43950acd8a9bc57a3e9eae1b | 7/7匹配 |
| p2_d2_worker0_tls_diagnostic_20261125_001 | c2b5cb9e5383accca0d4de2a110010e7ed306e6090328c9b0d8bef81d79a5916 | 8/8匹配 |

两份Manifest本身与已有证据一致，历史文件未改写或补写。
最近诊断的已确认事实：本地TCP约2.835ms，实际peer127.0.0.1:8000，Channel确认在
请求后约1.263秒，TLS失败Trace在约5.022秒；HTTP状态与Model ID均未取得。
现有证据不能区分SSH转发数据延迟与远端TLS服务响应迟缓，也不能证明证书错误。

## 3. 远端服务身份：历史与未知项

历史记录中的入口为risk-agent-remote，端口4035、root、专用审计密钥和严格known_hosts；
容器36f825bcc5da，Worker 0端口8000，Worker 1端口8001，Qwen3-8B/vLLM 0.34.0。
以上不是当前远端状态的证明。本次没有重新访问或检查任何Worker/GPU。

未来控制程序先用只读os.uname/getuid观察容器身份；若与本次批准的历史目标不同，
不发起TLS连接，返回DIAGNOSTIC_INCONCLUSIVE供审核。没有访问Worker 1。
即便TLS成功，本实验没有HTTP请求，也不会得到Served Model ID或确认GPU映射。

历史服务器证书为localhost，SAN包含DNS:localhost/IP:127.0.0.1，历史有效期从
2026-11-17至2029-02-19。此前严格CA/hostname验证成功，但当前远端证书状态仍UNKNOWN。

## 4. CA Bundle获取方案

已知历史远端CA路径：`/root/.config/risk-agent/tls/ca.crt`；部署脚本和既有预检支持
该路径曾存在。历史哈希与本地公开CA相同：
`97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`。

**当前实现采用优先方案1**：未来在远端读取这个已有公开CA，检查存在、可读且
SHA-256精确匹配，才将同一路径提供给-CAfile。不存在、不可读或身份不匹配时停止；
不使用本地WSL路径假装它也是远端路径，不替换任何CA文件。

备用方案2的无盘设计：经单独确认后，可将**公开CA PEM**作为SSH stdin结构化载荷，
在远端创建匿名RAM memfd，以/proc/self/fd/N和pass_fds供OpenSSL读取，随后关闭FD。
绝不传输API Key或私钥，不创建远端目录或持久化证书副本。

本轮已在WSL以现有公开CA的memfd执行一次**离线openssl verify**，证书链及localhost
hostname验证通过，证明本机OpenSSL 3.0.13可从匿名FD读取CA；这不是远端兼容性证据。
远端Python memfd能力和实际OpenSSL FD读取能力仍UNKNOWN。当前代码**未自动启用**
这一备用路径，CA缺失就停止，避免静默扩大传输行为或安全降级。

## 5. OpenSSL参数兼容性

- 实测本地WSL：OpenSSL 3.0.13（Library同版本）。
- 本地s_client -help包含：-connect、-servername、-verify_hostname、
  -verify_return_error、-CAfile、-brief、-state、-msg。
- 不依赖可选-trace；没有使用-verify 0、忽略错误或其他放松验证的参数。
- 本轮查到历史SSH日志中的OpenSSL 3.0.13是**本地OpenSSH版本横幅**，不是远端
  s_client版本证明。历史Ubuntu版本也不能替代实际工具版本。
- 实际远端OpenSSL版本/参数支持：UNKNOWN。这是Stage A最终阻塞项。

未来远端程序先运行仅查询元数据的openssl version和s_client -help；版本检查仅
接受经本地实现覆盖的OpenSSL 3.x，并要求全部8个参数实际存在。不兼容就停止，
不会尝试删去验证参数让握手“通过”。版本/help查询各有3秒进程等待上限。

## 6. SSH Remote Command设计

独立文件：

- `src/risk_agent/collection/remote_local_tls_isolation.py`
- `scripts/run_p2_d2_remote_local_tls_isolation.py`

入口模式为preflight（默认）和remote_tls_diagnostic。后者要求独立Stage B授权参数
及唯一Run ID；参数不代表用户授权，当前阶段不得调用。

命令使用risk-agent-remote，-T、-v、BatchMode、StrictHostKeyChecking、
ClearAllForwardings=yes、ForwardAgent=no、PasswordAuthentication=no、IdentitiesOnly、
ConnectionAttempts=1、ConnectTimeout=10、ControlMaster=no和ControlPath=none。
没有-L/-R/-D，没有Agent Forwarding；不复用既有ControlMaster。

本地`ssh -G`只做参数展开：端口4035、原专用IdentityFile和known_hosts、严格校验及
BatchMode生效，clearallforwardings=yes，未留下LocalForward/RemoteForward。
该检查没有建立SSH连接。

未来只创建一个SSH子进程，Remote Command为`python3 -B -`；stdin仅传送自包含、
stdlib-only的只读控制程序源码，不含凭据。-B禁止远端生成Python字节码文件。
控制程序只做必需的身份/工具/CA元数据检查，然后最多启动一个OpenSSL握手进程。
不执行HTTP请求、Agent、模型、目录创建、服务管理或GPU命令。OpenSSL stdin为DEVNULL，
程序源码不会成为发送给TLS服务的应用层数据。

SSH子进程记录PID、启动tick、UTC/单调起止时间、退出码、stderr及完整异常链。
日志标明local_wsl与remote_python时钟域；远端monotonic值不能与WSL值直接相减。
SSH认证日志接收时刻可给出本地观察到的认证耗时；不是精确网络RTT。无日志则unavailable。

## 7. TLS阶段Trace设计

未来唯一连接命令为：

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost \
  -verify_hostname localhost -verify_return_error \
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

| 事件 | 证据及限制 |
| --- | --- |
| TCP开始 | OpenSSL进程启动前的attempt事件；包含进程启动开销，不冒充socket syscall时间 |
| TCP完成 | 有CONNECTED日志时记录observed；仅有SSL_connect状态时标记inferred，而非精确connect结束时刻 |
| TCP失败 | 明确拒绝连接/不可达或非零connect errno；无证据时UNKNOWN |
| ClientHello发送 | -msg的>>> Handshake ClientHello概要 |
| ServerHello接收 | -msg的<<< Handshake ServerHello概要 |
| Certificate接收 | -msg的Certificate概要，不落盘证书hex dump |
| Certificate Verification | 明确Verification: OK或verify error/Verification error |
| Handshake Complete | 明确SSL negotiation finished successfully状态；仅有verify OK不算完成 |
| TLS Alert | -msg/-state的Alert概要 |
| Handshake Timeout | 外部deadline到期且TCP已观测、握手未完成；否则Inconclusive |
| 连接关闭 | 本次受控OpenSSL子进程结束/退出码；有Alert时另记录其关闭消息 |

所有远端事件附UTC、monotonic_ns和远端相对时间，本地记录接收时间。
只白名单保存状态及消息概要，不保存TLS hex dump、Session Master-Key或Ticket内容。
不能因为某阶段的日志缺失，就补造该阶段的精确开始/结束时刻。

## 8. 超时控制与可比性

OpenSSL参数中没有采用一个被误认为握手超时的TCP选项。采用已测试的Python外部
进程机制：**OpenSSL启动至结束共享5秒预算**。独立watchdog在deadline到期时只终止
本次Popen持有的诊断子进程，必要时等待0.25秒后强制结束同一子进程；记录退出码、
timed_out、真实单调耗时。审计回调阻塞不会阻止watchdog结束该OpenSSL进程。

终止和收集日志可增加清理耗时，不等于继续给握手等待时间。整体SSH命令另有40秒
本地安全边界，包含认证、元数据检查和回收；它不是额外TLS预算。

此前HTTPX把connect=5分别用于本地TCP连接和start_tls；本设计的5秒是总进程预算，
含TCP与工具启动。因此两者并非严格等价的纯握手时限。deadline前未取得TCP完成证据
时，不强行归为TLS握手超时；精确TCP耗时/独立TLS-only预算保留unavailable。

## 9. 分类与因果边界

| 分类 | 必要证据 |
| --- | --- |
| REMOTE_TCP_FAILURE | 明确连接拒绝/不可达/非零connect errno，且未观察TCP已完成 |
| REMOTE_TLS_HANDSHAKE_TIMEOUT | watchdog到期，TCP已观测而握手未完成 |
| REMOTE_CERTIFICATE_VERIFICATION_FAILURE | 明确verify错误；不以timeout代替 |
| REMOTE_TLS_SUCCESS | 严格验证命令退出0、非timeout、握手完成且Verification OK |
| DIAGNOSTIC_INCONCLUSIVE | 工具不兼容、身份或CA不符、缺少阶段证据、SSH整体超时等 |

SSH认证失败与Remote Command失败另行分类，不冒充远端TLS失败。
即使远端直连成功，也只能初步排除“该次远端TLS完全不可用”，不能直接认定SSH转发
是根因：工具、启动开销、时刻和监测行为均存在差异。

## 10. 离线测试与检查结果

```text
.venv/bin/python -m pytest -q tests/test_remote_local_tls_isolation.py --disable-warnings
.venv/bin/python -m compileall -q src/risk_agent/collection/remote_local_tls_isolation.py scripts/run_p2_d2_remote_local_tls_isolation.py tests/test_remote_local_tls_isolation.py
git diff --check
```

最终专项结果：22 passed in 0.77s。覆盖全部12类要求，并增加无TCP证据的deadline、
仅验证成功但握手未完成、CA哈希错误、前置参数缺失、时钟域隔离、慢审计watchdog等：

1. Mock OpenSSL远端TCP失败：正确分类。
2. TCP后握手超时：正确分类。
3. 明确证书错误：正确分类。
4. 握手与验证成功：正确分类。
5. Mock SSH认证失败：不发起第二次SSH。
6. Mock Remote Command失败：不归为TLS成功。
7. 版本/参数不兼容：不发起握手。
8. CA缺失：停止。
9. CA不可读：停止。
10. 受控终止：本地Python假进程在deadline后退出，未操作真实SSH/OpenSSL服务。
11. 审计写入失败：启动前fail closed；中途失败时回收本次本地假子进程。
12. Stage B无授权：在进程启动和Run创建前拒绝。

compileall、diff check通过。未重跑整个项目测试。Mock及本地进程测试不构成任何
远端TLS成功证据。preflight默认仅查询本地openssl version/help；由于远端证据不足，
明确输出Stage A BLOCKED并返回非零，而非把本地参数检查当成远端通过。

## 11. 未解决问题

- 实际远端OpenSSL版本及参数能力尚无可验证历史快照。
- 当前远端CA文件存在/权限/哈希、容器身份及8000服务状态均未重新确认。
- 远端输出的具体TCP/TLS可观测字段只能在获批后确认；缺失不得伪造。
- 备用公开CA的memfd方案只在WSL本地离线验证，未在远端启用或测试。
- 需要先补足真实版本/help证据，才能满足用户定义的完整Stage A PASS条件。
  可由用户提供已有的可信只读输出，或单独授权只读元数据预检；本轮不会自行执行。

## 12. Stage B执行方案（未授权、未执行）

仅在新的明确授权 `授权执行 P2-D2 Remote-Local TLS Isolation Test Stage B` 后，
并解决Stage A兼容性证据阻塞时，才执行remote_tls_diagnostic模式。
以唯一Run ID创建独立产物目录，不复用任何旧Run。
未来产物设计包含run_manifest.json、ssh_remote_command_audit.jsonl、gate_summary.json、
resource_release.json和artifact_manifest.json。远端各阶段JSON事件嵌套保留在SSH审计中，
连同本地接收时间记录；CA/工具前置失败也保留，不伪造已经发生的握手。

同一SSH Remote Command先核实身份、OpenSSL能力和已有CA，全部通过后一次握手。
不发送GET，不调用Qwen，不启动/停止Worker，不创建Tunnel，不自动重试。
任何元数据门禁失败就停止；如果需要先读取版本/help，应取得相应的明确只读授权，
不把Stage A或CLI参数当成该授权。

## 13. Git与变更范围

HEAD为bfd74dacf523d4677bc662e2e92b3bd5c8c09a10，main；本地origin/main为12e4792，
未fetch。原有4个Stage A/Worker 0诊断相关未提交文件均保留，未覆盖。

本轮新增4个文件：独立模块、入口、tests/test_remote_local_tls_isolation.py和本报告。
没有修改生产ConnectionLifecycle、SSHProcess、Checkpoint、预算/模型配置、SSH配置、
CA、Key、历史Run或历史报告。全部差异保留供审核，未暂存、Commit或Push。

两份冻结配置仍匹配原始哈希；历史7+8项产物以及两份Manifest本身均匹配。
未创建真实remote-local isolation实验目录。

## 14. 资源使用与停止

真实SSH、远端命令、Tunnel、TLS握手、HTTP/HTTPS、GET、Qwen、GPU/vLLM操作均为0。
仅运行本地OpenSSL版本/help/离线证书验证、Mock Runner及本地Python超时假进程。
后者已受控退出，公开CA的本地匿名FD已关闭，无远端文件或进程变动。

本轮结束。最终Gate保持REMOTE_TLS_ISOLATION_STAGE_A_BLOCKED，不自动进入Stage B。

## 追加：2026-11-25 Remote OpenSSL Metadata Preflight

以上保留初版Stage A的证据状态。本次获得单独的“一次只读远端元数据检查”授权后，
新审计目录p2_d2_remote_openssl_metadata_20261125_001已取得实际远端证据：
容器36f825bcc5da，root，/usr/bin/openssl为OpenSSL 3.0.2，必需的8项s_client参数
均受支持。历史CA路径存在且可读；本次只检查元数据，没有读取CA内容或进行握手。

原版本/参数门禁已用真实记录离线验证可接受，无需修改原握手实现。
当前人工审计结论更新为 **REMOTE_TLS_ISOLATION_STAGE_A_PASS**，解除初版的唯一
版本/参数证据阻塞。完整证据及参数矩阵见
[Remote OpenSSL Metadata Preflight报告](p2_d2_remote_openssl_metadata_preflight_report.md)。

默认离线preflight没有自动读取新审计证据，仍会保守输出UNKNOWN；不将该CLI旧输出
伪称为已经更新。进入Stage B仍需独立授权，且运行时身份、工具和CA哈希门禁仍须
通过。本次没有TLS连接，也没有执行Stage B、Commit或Push。
