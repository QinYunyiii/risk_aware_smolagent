# P2-D2 Worker 0 Single-Request TLS Diagnostic — Stage B

## 最终 Gate

`TLS_DIAGNOSTIC_BLOCKED`

分类：`B_TCP_COMPLETE_TLS_INCOMPLETE`。
唯一一次GET尝试的本地TCP连接成功，SSH direct-tcpip Channel有打开确认，但TLS握手
未在5秒connect预算内完成。没有HTTP状态、Model ID或证书验证错误。没有第二次请求。

本次直接观测缩小了故障范围，但未证明具体根因。不能将此结果改判成90秒Smoke、
Worker 1或完整五任务Gate的成功。

## A. Git 与冻结实现

- 执行HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，main。
- 本地origin/main仍为`12e4792e2af516636aacd756056e7dbc5e7a41aa`；本轮未联网更新Git引用。
- 开始时有3个已知Stage A未提交文件：诊断入口、专项测试及Stage A报告。
  本次没有修改它们，仅新增本Stage B报告和被Git忽略的唯一Run。
- 入口SHA-256：`2f6204853a9e925979a17995a62252bf6732cd8e571a45169092996a054f8064`。
- 专项测试SHA-256：`21d5a0d48f5d68e816970c06c8f4db1d3a23fd4aa1d2bd3d77f74e3ce1cc4756`。
- 真实执行后入口哈希与Run Manifest、执行前哈希完全一致。
- 未Commit、未Push，保留现有提交和用户审核状态。

## B. 环境、资源与TLS身份

- 用户明确授权本次Stage B；Run ID：`p2_d2_worker0_tls_diagnostic_20261125_001`。
- 实际Client：httpx2 2.16.2，Transport：httpcore2 2.16.2；OpenAI SDK 3.52.0、
  smolagents 1.26.0未用于执行本次GET模型封装或Agent。
- 沿用失败Smoke的直接httpx2 GET路径。Client与Transport均trust_env=False，
  显式retries=0，禁止重定向，connect=5，read/write/pool各10秒。
- Endpoint为`https://localhost:8000/v1/models`；SNI实际Trace记录为localhost。
- CA路径：`/home/three/.config/risk-aware-smolagents/pki/ca.crt`。
  SHA-256：`97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`。
- Trace记录check_hostname=true、verify_mode=2（CERT_REQUIRED）。没有降级或绕过校验。
- API Key沿用已有环境来源；只检查存在状态，未生成、替换或持久化密钥值。
- SSH配置、专用密钥指纹及known_hosts本地预检通过；8000端口启动前空闲。
- 仅一个自有WSL OpenSSH子进程：PID 963583，/proc启动tick为31295202；
  数值直接取自ssh_process_audit中的identity。
  启动参数为Stage A冻结的-F /dev/null、-vvv、-N -T、专用identity、4035端口、
  StrictHostKeyChecking、BatchMode、IdentitiesOnly、禁止密码回退和一个8000 LocalForward。
  保留批准的配置隔离替代，没有ClearAllForwardings=yes，没有8001转发。
- Worker 0是本次逻辑目标；远端当前模型、既有Worker PID和物理GPU映射没有通过
  新的远端资源审计独立确认。不能由本地8000监听反推模型身份或GPU 0状态。

## C–G. 关联时间线及请求结果

T=0取worker_probe_audit中`https_request_started`记录的monotonic_ns：
`312954666677641`；UTC为`2026-11-24T18:13:39.057851+00:00`
（2026-11-25 02:13:39 Asia/Shanghai）。
下面均由原始monotonic_ns计算；不是计划时间或模拟值。

| 相对T0（秒） | 实际事件 | 来源 |
| ---: | --- | --- |
| -2.644855 | SSH子进程启动记录 | ssh_process_audit |
| -0.059723 | 专用公钥认证完成 | SSH stderr接收时间 |
| -0.059654 | 127.0.0.1:8000监听ready | SSH stderr接收时间 |
| +0.000000 | 唯一HTTPS请求开始 | worker_probe_audit |
| +0.002031 | connect_tcp.started，timeout=5 | HTTPX/HTTPCore实际Trace |
| +0.004866 | connect_tcp.complete | HTTPX/HTTPCore实际Trace |
| +0.005179 | 8000转发请求，目标127.0.0.1:8000 | SSH stderr接收时间 |
| +0.005225 | Channel 1 new direct-tcpip | SSH stderr接收时间 |
| +0.005226 | 紧接Channel创建的send packet type 90 | 原始SSH日志，单direct-channel关联 |
| +0.007205 | start_tls.started，SNI=localhost，timeout=5 | HTTPX/HTTPCore实际Trace |
| +1.262835 | receive packet type 91 | 原始SSH日志 |
| +1.262838 | Channel 1 open confirm | SSH stderr接收时间 |
| +5.018445 | Channel 1本地read报Broken pipe | 原始SSH日志 |
| +5.018489 | Channel 1 shutdown_read/close观察 | SSH stderr接收时间 |
| +5.021816 | start_tls.failed：ConnectTimeout | HTTPX/HTTPCore实际Trace |
| +5.067974 | 请求结束；HTTP状态null，Model ID为空 | worker_probe_audit |
| +5.080104 | 清理阶段Channel 1 free，完整来源/目标元数据 | SSH stderr接收时间 |
| +5.096473 | 本次SSH子进程退出，code=0 | 本地进程审计 |

### TCP与TLS测量

- TCP阶段Trace间隔：约2.835毫秒；本地TCP连接确实完成。
- TLS开始至失败Trace间隔：约5.014610秒。配置仍为5秒；观测间隔包含调度、
  异常传播和审计开销，不表示配置被延长。
- 单GET调用记录总耗时：`5.067972042`秒。
- 原始请求UTC起止之差仅为2.423085秒，与对应monotonic间隔5.067974秒相差约
  2.644889秒；Channel确认至TLS失败的UTC/monotonic间隔也存在相同量级差异。
  这是壁钟与单调时钟关系变化的可观测证据，具体来源未核验，不能据此认定TLS
  超时由壁钟变化造成。本文所有耗时和关联顺序均采用monotonic，不用UTC差值代替，
  也不更改原始UTC记录。
- Channel open request/type90到open confirm：本地日志接收间隔约1.257612秒。
- 从TLS开始到收到Channel确认约1.255633秒；确认后至TLS失败Trace约3.758977秒。
  这些不是剥离了日志读取、网络和调度的纯远端connect耗时。
- 只有4条TCP/TLS Trace：connect started/complete、TLS started/failed。
  没有TLS complete、HTTP请求头发送或响应接收Trace。GET是一次客户端调用尝试，
  不能说服务器应用层已接收到一条完整HTTP请求。

### 完整异常证据

早期start_tls.failed回调保存：

```text
httpcore2.ConnectTimeout: _ssl.c:983: The handshake operation timed out
  caused by TimeoutError: _ssl.c:983: The handshake operation timed out
```

调用层最终保存：

```text
httpx2.ConnectTimeout
  caused by httpcore2.ConnectTimeout
    caused by TimeoutError: _ssl.c:983: The handshake operation timed out
```

脱敏traceback指向httpcore2同步后端的start_tls / ssl_context.wrap_socket路径。
没有SSLCertVerificationError，也没有HTTP ReadTimeout。ClientHello是否已发完、
ServerHello/Certificate是否部分到达，仍无TLS record级证据，标记unavailable。

注意：SSH日志中的本地read关闭比`start_tls.failed`回调早约3.3毫秒，并不能证明
“SSH先主动关闭导致TLS超时”。同步TLS后端在抛出异常前会关闭本地socket；Trace失败
事件又发生在异常离开该层时。该顺序与客户端超时后的socket关闭一致。日志接收时间
也不是精确的内核发生时刻，不能仅用这3.3毫秒建立因果方向。

## H. 三种实际地址

| 对象 | 实测信息 | 证据边界 |
| --- | --- | --- |
| HTTPS Client TCP peer | server=127.0.0.1:8000；client=127.0.0.1:47275 | 真实HTTPCore stream extra info |
| SSH控制连接 | peer为本次批准的实验入口:4035；local为本机出站地址:44317 | 自有SSH FD inode与/proc TCP表；完整地址保留resource_release，本报告不重复出站IP |
| SSH direct-tcpip | Channel 1，目标127.0.0.1:8000，来源127.0.0.1:47275 | SSH最终channel free行同时包含来源端口和目标 |
| 远端HTTPS服务实际socket peer | unavailable | 未远程执行getpeername或检查远端socket |

来源端口47275把这条HTTPX连接与Channel 1直接关联，而非与额外健康探测连接混淆。
本次使用Stage A冻结的被动/proc健康观察，没有主动connect_ex观测流量。

## I–J. 故障分类、根因及置信度

**CONFIRMED**：

- Case B：本地TCP完成、TLS开始、TLS未完成；不是TCP连接拒绝或HTTP响应超时。
- 实际本地peer为IPv4的127.0.0.1:8000，排除本次连接误落IPv6或其他端口的推断。
- 对应direct-tcpip channel有open confirmation，映射未串线。
- TLS证书/主机名校验保持开启，但没有证书验证错误；服务模型身份仍未取得。
- Channel确认到达之前，TLS预算的本地观察窗口已过去约1.26秒。

**POSSIBLE / MEDIUM**：SSH通道建立/传输延迟或远端TLS响应迟缓消耗了5秒预算。
Channel确认到达后仍约有3.76秒直到握手失败；因此不能把全部超时归因于Channel打开。

**UNDETERMINED**：SSH数据流调度、远端服务握手进度、网络丢包/停顿、Worker负载、
实际TLS返回字节和5秒预算是否足以完成健康握手。没有直接因果证据锁定单一根因。

本次没有旧Smoke的主动观测连接，但相似超时仍发生；这说明该观察器流量不是解释
本次超时的必要条件。两次不是同条件随机对照，不能据此证明它在旧Run中没有影响。

gate_summary中的audit_completeness.tls_trace=false指“没有TLS complete事件”，
不表示失败Trace未采集；本次start_tls.failed及底层异常证据实际齐全。
不修改冻结分类字段或历史Run来调整这一含义。

## K. 资源释放与执行范围

- 仅创建并关闭一个本次SSH子进程；启动tick、实际命令行及expected_identity用于清理核验。
- manager.close执行后退出码0；关闭前控制连接TCP state为01（ESTABLISHED）。
- 本次8000端口已释放，随后另一次被动本地TCP表检查也未见8000 LISTEN。
- 远端Worker PID、GPU状态及其他SSH连接没有被本任务修改；没有发送远端进程控制、
  服务重启或GPU命令。其远端当前状态未另行查询，不冒称已经实测PID/GPU完全不变。
- GET尝试1；自动/手动重试0；重连0；Failover0；Worker 1访问0；Completion0。
- 没有90秒Smoke、Agent任务、Checkpoint、五任务Gate或P2-D3。

## L. Artifact Manifest、测试与安全检查

权威Run：
`runs/phase2_p2_d2_tls_diagnostic/p2_d2_worker0_tls_diagnostic_20261125_001/`

8项被Manifest覆盖的文件：
run_manifest.json、ssh_process_audit.jsonl、ssh_channel_trace.jsonl、
tcp_tls_trace.jsonl、worker_probe_audit.jsonl、connection_audit.jsonl、
resource_release.json、gate_summary.json。Manifest不包括自身。

- 8/8 SHA-256复算匹配。
- Manifest自身SHA-256：`c2b5cb9e5383accca0d4de2a110010e7ed306e6090328c9b0d8bef81d79a5916`。
- 冻结入口及两份配置执行后哈希与执行前、Run Manifest一致。
- 原始Smoke Manifest本身及7项历史产物再次复算匹配，未覆盖或补写。
- 原始审计本地检查未发现当前API Key值或OpenSSH私钥标记；未提交原始数据。
- 执行后的离线专项测试：`15 passed in 1.16s`。
- 入口compileall与git diff --check通过。无代码改动，不重复Stage A的359项全仓回归。

实际执行命令：

```text
.venv/bin/python scripts/run_p2_d2_worker0_tls_diagnostic.py --authorize-stage-b --run-id p2_d2_worker0_tls_diagnostic_20261125_001
.venv/bin/python -m pytest -q tests/test_worker0_tls_diagnostic.py --disable-warnings
.venv/bin/python -m compileall -q scripts/run_p2_d2_worker0_tls_diagnostic.py
git diff --check
```

## M. 下一步建议与停止

不直接重跑本请求、90秒Smoke或五任务Gate，也不提高现有Timeout。
若用户希望继续缩小范围，优先另行批准一次**仅Worker 0远端loopback严格TLS的只读
对照**，仍保持5秒预算、零Completion/重试，以区分SSH路径与服务本机TLS响应。
或者由用户单独选择一次超时预算对照；不能在同一Run中连续执行两种方案。

本次Stage B已停止。保留全部原始证据，等待审核；未Commit、未Push。
