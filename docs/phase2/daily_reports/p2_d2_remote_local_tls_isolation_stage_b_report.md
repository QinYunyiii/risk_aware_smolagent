# P2-D2 Remote-Local TLS Isolation — Stage B

## 执行摘要

日期：2026-11-25（Asia/Shanghai）。Run ID：
`p2_d2_remote_local_tls_isolation_20261125_001`。

冻结实现的实际分类：**DIAGNOSTIC_INCONCLUSIVE**。
没有修改分类器或将结果改判为 REMOTE_TLS_SUCCESS。

本次一次SSH Remote Command内，容器身份、OpenSSL参数、可信CA SHA-256及
Worker 0监听/进程检查通过。唯一OpenSSL进程约0.119948秒后正常退出0，未超时；
观测到ClientHello、ServerHello、Certificate、读/写Finished状态、Verification: OK
和本端close_notify。但原分类器需要的特定握手完成标记未出现，state.complete=false，
因此最终保持不确定分类。这是本次实际输出与冻结完成判据的可观测性缺口，
不是证书验证失败或5秒握手超时的证据。

没有第二次SSH、第二次TLS握手、HTTP、Completion、Tunnel、Worker切换或重试。
诊断进程已回收，Worker 0前后被动元数据一致。到此停止，等待审核。

## 授权与实现边界

使用用户本轮Stage B授权；前置OpenSSL元数据证据见
[预检报告](p2_d2_remote_openssl_metadata_preflight_report.md)。
原Stage A源码、测试、入口及冻结模型/实验配置均未修改。

执行使用原run_authorized、SSHRunner、remote_entry和bounded_process。
为满足本轮明确增加的“执行前核验Worker 0端口及服务状态”，仅在传入的内存程序中
增加一个只读/proc检查包装：原身份/工具/CA门禁之后、OpenSSL启动之前核验监听与
持有该socket的vLLM进程；握手后再做一次同样的被动读取。未创建额外探测连接，
未修改远端文件；原OpenSSL命令、验证参数、分类器和5秒进程预算保持原样。
本地保存了实际发送源码及执行驱动，供复核此补充检查。

注意原tcp_connect_attempt_started事件在调用probe前发出，因而本次位于附加服务
检查之前。它是“尝试意图”，不是精确socket建连起点；实际OpenSSL启动时间应取
process_outcome.started_monotonic_ns。附加服务检查不计入5秒OpenSSL预算。

## SSH身份、参数与异常链

- Alias：risk-agent-remote；目标root@121.48.161.243:4035。
- 专用IdentityFile及UserKnownHostsFile均由本地ssh -G确认；该展开不建立连接。
- 客户端公钥指纹：SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A。
- 实际SSH诊断确认该key被接受，以publickey认证；服务端host key匹配严格known_hosts。
- BatchMode=yes、StrictHostKeyChecking=yes、IdentitiesOnly=yes、PasswordAuthentication=no、
  ClearAllForwardings=yes、ForwardAgent=no、ConnectionAttempts=1、ConnectTimeout=10。
- ControlMaster=no、ControlPath=none；无-L/-R/-D，无历史转发；Remote Command为python3 -B -。
- SSH子进程未继承API Key；不读取或传输凭据、证书私钥。
- 本地SSH PID 973862，start_ticks=31614533；退出码0、timed_out=false、已wait回收。
- 本地UTC开始2026-11-24T19:02:25.887641+00:00，结束19:02:32.299548+00:00；
  对应北京时间2026-11-25 03:02:25.887641至03:02:32.299548。
- 单调时钟耗时6.411906397秒；认证日志接收约在启动后3.305835秒，包含接收/调度开销。
- 无捕获到的SSH或远端Python异常链；stderr不是空文件，包含完整已接收的SSH-v日志，
  末尾Exit status 0。最终不确定分类是正常返回结果，不应伪造为SSH异常。

## 前置核验

| 项目 | 实测证据 |
| --- | --- |
| 容器/用户 | hostname=36f825bcc5da，uid=0，与冻结门禁相符 |
| OpenSSL | 3.0.2，8个必需参数全部存在 |
| 原远端CA | /root/.config/risk-agent/tls/ca.crt，可读取 |
| CA SHA-256 | 97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef，与可信基准一致 |
| Worker 0监听 | 127.0.0.1:8000，LISTEN，socket inode=1746615764 |
| 持有监听的进程 | PID=382420，comm=vllm，state=S，start_ticks=519951147 |
| 服务参数身份 | 仅在内存检查vLLM及--port 8000；均匹配，完整cmdline未输出 |
| 前后对照 | 监听、PID、start_ticks、命令摘要及状态全部相同 |

这些证据证明本次有对应的活进程持有预期监听；不证明模型请求健康。
本次没有查询GPU、服务模型ID或模型负载，也未连接Worker 1。

## TLS命令与完整阶段审计

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost \
  -verify_hostname localhost -verify_return_error \
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

OpenSSL stdin为DEVNULL，不发送HTTP或应用请求。CA、证书链和hostname严格验证，
没有验证降级。外部独立watchdog保持5秒总进程预算，包含进程启动/TCP/TLS，
不是独立的5秒TLS-only预算；本次未触发watchdog。

远端OpenSSL PID 401965，start_ticks=566636256，返回码0，timed_out=false，已回收。
远端UTC开始19:02:30.715790，结束19:02:30.835740；单调耗时0.119948355秒。

| 阶段 | 相对OpenSSL启动的观测时间 | 证据/限制 |
| --- | --- | --- |
| TCP完成 | 约108 ms | 从SSL_connect状态推断；无精确socket时间 |
| ClientHello发送 | 115.057 ms | >>> TLS 1.3, Handshake, ClientHello |
| ServerHello接收 | 116.179 ms | <<< TLS 1.3, Handshake, ServerHello |
| Certificate接收 | 116.681 ms | <<< TLS 1.3, Handshake, Certificate |
| Finished状态 | 约117–118 ms | read finished及write finished状态 |
| 证书验证 | 117.923 ms | Verification: OK；严格hostname验证参数生效 |
| 本端关闭通知 | 118.092 ms | warning close_notify；不是fatal证书告警 |
| 特定完成标记 | unavailable | 未出现分类器所需SSL negotiation finished successfully |
| 进程退出 | 119.948 ms | rc=0，未超时 |

以上为远端同一时钟域的日志观测时间，包含读取/解析调度开销，不能当成逐字节网络时间。
local_wsl与remote_python单调时钟分域保留，不跨主机相减。

完整保存了原Stage A白名单允许的全部阶段事件及实际SSH stderr；没有TLS十六进制
dump、Session Master-Key或Ticket内容。这里“完整阶段审计”不等于未经筛选的OpenSSL
全部字节输出：冻结实现本来就只保存状态/消息概要，不能事后补造被排除的字段。
HTTP状态、Model ID、精确TCP耗时和握手完成特定事件均无相应证据。

## 故障解释与未解决问题

1. 本次远端loopback连接没有复现此前5秒超时，并收到了服务端握手消息和严格验证OK。
   证据强烈支持该时刻远端TLS能够响应，不能据此直接认定SSH转发路径是历史根因。
2. 冻结完成判据只接受一个特定SSL_connect字符串，本次实测未输出该字符串；
   虽有write finished、Verification: OK及退出0，分类器仍保守返回DIAGNOSTIC_INCONCLUSIVE。
   原结果state为tcp=true、hello=true、verified=true、complete=false，原样保留。
3. 不把“缺少完成事件”写成已经确认的TLS失败，也不以人工推断覆盖冻结Gate。
   不修改实现、不补跑网络测试、不延长预算。
4. 下一步可先独立审核现有trace与完成判据，做离线fixture验证；任何判据改动及再次
   网络验证应另行批准，本轮不自动实施。原HTTPX/SSH转发失败根因仍未确定。

## 证据、完整性与资源释放

产物目录：
`runs/phase2_p2_d2_remote_local_tls_isolation/p2_d2_remote_local_tls_isolation_20261125_001/`。

- run_manifest.json、evidence_baseline.json：授权范围、Git状态、冻结源码/配置哈希、历史基线。
- remote_stdin_program.py、worker_guard.py：实际远端内存程序和只读附加检查源码。
- ssh_remote_command_audit.jsonl：本地接收时间、25条远端事件、完整SSH stderr与退出信息。
- tls_trace.jsonl、ssh.stderr.txt：从同一原始审计派生的可读记录，无新增请求。
- gate_summary.json：冻结实现实际不确定分类；resource_release.json：本次进程回收证据。
- integrity_verification.json：历史文件/Manifest和冻结文件前后校验。
- artifact_manifest.json：初始10项产物清单；后续补充分析另写追加清单，不覆盖原Manifest。

只回收本次持有的子进程：SSH和OpenSSL均退出0、已wait；本地SSH PID已不在/proc。
Worker前后监听/进程元数据一致，未执行任何服务管理命令。没有新建Tunnel或监听端口，
无此类资源需要释放。未通过额外SSH确认清理，避免超出一次命令限制。

## 本地测试与Git状态

- 实验前专项测试：22 passed in 1.10s；实验后：22 passed in 0.77s。
- compileall及git diff --check通过；未重跑全仓测试。
- 补充只读检查用本地Mock验证监听/进程匹配、缺失监听立即停止以及秘密不落日志。
  初次本地测试夹具复用了已关闭的StringIO导致失败，修正夹具后通过；未改远端实现，
  未因此重试任何SSH或TLS请求。
- 历史Run内容和Manifest、两份冻结配置及3份Stage A实现/测试文件前后哈希均匹配。
- HEAD保持bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。
- 既有9个未跟踪文件保留；新增本报告，原始证据位于Git忽略的runs目录。
- 未暂存、Commit、Push；未修改历史Run、远端Worker或研究协议。

## 停止状态

**DIAGNOSTIC_INCONCLUSIVE**。一次授权诊断已完成，资源释放检查通过。
不自动进入Smoke、完整Gate或P2-D3，等待用户审核。
