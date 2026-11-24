# P2-D2 TLS Handshake Forensic Audit — Stage A

## 1. 执行摘要

最终 Gate：`PARTIALLY_IDENTIFIED`。

已确认失败层为 **客户端 TLS 握手阶段耗尽 5 秒 connect 预算**。它不是已观测到的
HTTP 响应超时、TCP 拒绝连接或证书校验错误。代理绕行、旧 HTTP 连接复用及端口映射
混淆缺乏支持证据。SSH 隧道进程和本地监听在失败时仍存活，但这不能证明远端目标
端口或 TLS 服务及时响应。

最可能的问题区域是：SSH 转发数据路径或远端 TLS 握手响应未能在较短的 5 秒预算内
完成。预算过短是有证据的候选影响因素，具体延迟来源及因果根因仍 UNKNOWN。
不据此修改超时、替换 CA、改判 Smoke PASS 或扩大基础设施改造。

本轮仅读取本地证据并新增本报告；没有 SSH、Tunnel、HTTP/HTTPS 请求或远端操作。

## 2. 历史证据完整性

- 审计基线：main，HEAD/origin/main 均为
  `12e4792e2af516636aacd756056e7dbc5e7a41aa`；开始时工作区干净。
- 权威失败 Run：`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261125_001/`。
- 原始 Artifact Manifest：7 个文件，逐项 SHA-256 和大小全部匹配。
- Manifest 本身 SHA-256：
  `af3ae0b5dd8d0f2f9fbec9518ffeb43e85663e2e43950acd8a9bc57a3e9eae1b`。
- Run 中绑定的 Smoke 脚本和 ConnectionLifecycle SHA-256 均与当前本地实现匹配。
- Run 记录的执行 HEAD 为 `88a465f`，当时新入口尚未提交；其实现哈希与后续
  `12e4792` 提交中的文件一致，不能把 Run 的 git_commit 字段改写成后续提交。
- 两份冻结配置的当前 SHA-256 与 Run 记录一致：
  - Full Gate：`3d3c5f161f9fa13c518e00a193eb54e74452a43c3ecb36f029d8df2986a5d3b4`
  - Remote 1024：`4731cb56c128e3c150712745e25b9881fd8c19d64a37fd87a97f48ae3a5a57a5`
- 没有改写、补写或重新生成失败 Run 中的任何文件。

## 3. TLS 异常链和调用位置

Worker：worker-0；请求为计划中的第一次 GET `/v1/models`。

| 字段 | 权威记录或代码事实 |
| --- | --- |
| 请求开始 UTC | 2026-11-24T17:40:12.427870+00:00 |
| 请求结束 UTC | 2026-11-24T17:40:17.564583+00:00 |
| 单调时钟耗时 | 5.136730799044017 秒 |
| HTTP 状态 / Model ID | null / 空列表 |
| TCP connect 超时 | 5 秒 |
| TLS handshake 超时 | 复用 connect=5 秒；无独立握手超时设置 |
| HTTP read / write / pool 超时 | 各 10 秒 |
| 请求整体总时限 | 没有单一整体 10 秒 deadline；HTTPX 超时按操作/阶段应用 |
| SSH 认证建连超时 | 另一个独立的 SSH ConnectTimeout=15 秒 |
| HTTP 响应阶段 | 未进入；该路径在完成 TLS 后才发送 HTTP 请求 |

异常类型链保留为：

```text
ConnectTimeout: _ssl.c:983: The handshake operation timed out
  caused by ConnectTimeout: _ssl.c:983: The handshake operation timed out
    caused by TimeoutError: _ssl.c:983: The handshake operation timed out
```

原始 JSON 没有保存完整 traceback、模块限定名、TLS record 或 socket peer。
以下位置是与原始实现哈希匹配的代码及当前安装依赖推导的调用链，不冒充已保存的
运行时堆栈：

1. `scripts/run_p2_d2_connection_smoke.py:102` 委派 `client.get()`；第 106 行读取 Future
   结果并传播异常；第 181 行设置 `httpx2.Timeout(10, connect=5)`。
2. `httpcore2/_sync/connection.py:118` 完成 connect_tcp 后，才在第 141 行进入
   start_tls；第 138 行使用 origin host 作为 server_hostname，第 139 行复用 connect 超时。
3. `httpcore2/_backends/sync.py:163` 将 socket 超时设为 5 秒，第 164 行调用
   SSLContext.wrap_socket，默认在连接时完成握手。
4. `/usr/lib/python3.12/ssl.py:1320` 调用底层 `_sslobj.do_handshake()`。
   记录中的 `_ssl.c:983` 是编译模块报错位置，未读取或假定另一个版本的 C 源码。
5. 同步后端把 socket.timeout/TimeoutError 映射为 httpcore2.ConnectTimeout；
   HTTPX Transport 再映射为 httpx2.ConnectTimeout，保留 cause 链。

完全离线的 FakeSocket/FakeContext 测试重现了这条映射；另一个模拟证书错误映射为
ConnectError → ConnectError → SSLCertVerificationError，与本次异常链不同。

### 握手阶段区分

| 候选异常阶段 | 本次结论 |
| --- | --- |
| TCP Connect Timeout | 不符合底层握手报错；本地 TCP 已进入 SSL wrap/握手路径，高置信度推断 |
| TCP Connection Refused | 未观察到，异常链不匹配 |
| ClientHello 未成功送出 | UNKNOWN；无 TLS record/字节计数证据 |
| 等待 ServerHello 超时 | POSSIBLE，不能与其他握手等待阶段区分 |
| Certificate Verification Error | 未观察到，不得据此换 CA 或放松验证 |
| TLS Handshake Timeout | CONFIRMED：底层错误消息和超时映射支持 |
| HTTP Response Timeout | 不支持；HTTP 状态 null，失败发生在 TLS 握手完成之前 |

“本地 TCP 已建立”不等于“远端 loopback 的 TCP/TLS 服务已被成功连接”。SSH 会先
接受本地连接，再建立 direct-tcpip 转发通道；后者的建立及数据往返没有独立时间戳。

## 4. HTTP Client / Proxy 审计

当前安装：OpenAI SDK 3.52.0、httpx2 2.16.2、httpcore2 2.16.2、smolagents 1.26.0。
传统名称 `httpx` / `httpcore` 未安装。本轮直接 GET 使用 httpx2，没有经过 OpenAI
SDK；SDK 在本次请求中没有自己的 TLS Context。OpenAI 的本地实现也导入 httpx2。

| 代理变量 | 当前是否存在 |
| --- | --- |
| HTTP_PROXY / http_proxy | 是 / 是 |
| HTTPS_PROXY / https_proxy | 是 / 是 |
| ALL_PROXY / all_proxy | 否 / 否 |
| NO_PROXY / no_proxy | 否 / 否 |

只检查存在状态和路由，没有输出代理 URL、用户名、密码或 Token。
当前环境没有显式 localhost 的 NO_PROXY 规则；但这并不导致本次 Smoke 使用代理：

- Smoke 的 Client 和 HTTPTransport 均 `trust_env=False`，没有显式 proxy 或 mounts。
- 已安装 Client 的 `_client.py:653` 仅在 trust_env 且无显式 transport 时启用环境代理。
- Transport 的 `_transports/default.py:150` 在 proxy=None 时构造直连 ConnectionPool。
- 离线构造相同 Client 后断言 localhost 路由指向该 Transport、代理 mounts 为零、
  连接池初始为空、retries 为零、TLS Context 对象与传入对象相同，均通过。
- 历史 Run 未快照代理变量；“变量存在/缺少 NO_PROXY”属于当前环境观察，历史
  请求绕过环境代理的结论来自哈希匹配的构造代码，而不是假设环境从未变化。

因此 **环境代理劫持本次 localhost 请求不受当前代码路径支持**。不建议为了此故障
修改代理变量。Client 为本次 Run 新建，首个请求无旧连接可复用；只有一个应用 GET
在单工作线程中执行。主线程与监测线程同时观察连接，但没有并发模型/HTTP请求。
HTTPTransport 显式 retries=0，未跟随重定向，没有 SDK 自动重试。

## 5. SSH 转发路径审计

resource_release 中保存的实际 Popen args 与当前 SSHProcess 实现一致：

| Worker | PID | 绑定地址与端口 | SSH 远端目标端口 | 退出码 / stderr |
| --- | ---: | --- | --- | --- |
| worker-0 | 956316 | 127.0.0.1:8000 | 127.0.0.1:8000 | 0 / 0 bytes |
| worker-1 | 956329 | 127.0.0.1:8001 | 127.0.0.1:8001 | 0 / 0 bytes |

两个独立子进程，distinct tunnel ID、process-start identity 与 Run 归属，未复用
ControlMaster。身份 token 是管理器生成的句柄代际标记，不是内核进程启动时间。

- 实际参数为 `-F /dev/null`，隔离用户及系统 ssh_config；没有额外 -F/Include、-R、-D。
  参数语义和记录证明配置隔离，未保存进程 open-file trace，故不声称有系统调用级证明。
- 每个进程一个显式 -L，总共两个；本地/远端端口一致，无 Worker 交换迹象。
- 保留严格主机校验、BatchMode、IdentitiesOnly、指定 known_hosts 和
  ExitOnForwardFailure。`ClearAllForwardings=yes` 的省略是用户批准的替代方案。
- 两个进程各有 41 条健康样本，均 ALIVE/LISTENING；没有 lost 事件。
- 退出发生在显式 cleanup_started 与 cleanup_finished 之间，之前均 ALIVE；
  因此没有证据表明本次握手超时由 SSH 进程提前退出引起。
- 端口观察仅证明本地 connect_ex 可完成，不证明 SSH 通道已打开、远端端口可达或
  TLS 服务健康。SSH stderr 为空也不能证明每次转发的数据流正常。

### 观察器的主动 TCP 连接副作用

`connection_lifecycle.py:127` 的 snapshot() 每次调用 socket.connect_ex，而不是
被动查询 LISTEN 表；每次 event() 又会调用 snapshot。后台与 Smoke 主线程均在观察。
请求起止之间，worker-0 有 40 个、worker-1 有 39 个已记录事件，因此至少触发了
79 次本地 TCP connect 尝试，另有未独立记录的直接 snapshot 调用。

这些 connect 后立即关闭的连接可能形成额外 SSH 通道及远端未完成 TLS 会话；
实际远端接受数、排队和资源影响 UNKNOWN。只能确认观察器具有主动连接副作用，
不能把它直接写成已确认根因。本轮未修改监测实现。

## 6. TLS 身份配置与历史成功对照

- URL 固定为 `https://localhost:8000/v1/models`；scheme=https，origin host=localhost。
- 代码没有自定义 sni_hostname 扩展，因此依赖将 localhost 传入 SSL server_hostname；
  实际 ClientHello/SNI 的 wire 内容未记录。
- 本地转发仅 IPv4。当前 `/etc/hosts` 将 localhost 指向 127.0.0.1，::1 的别名为
  ip6-localhost/ip6-loopback。没有历史 getaddrinfo 结果或实际 peer address，不能完全
  证明失败 socket 的地址族；现有证据不支持优先认定 IPv6/其他监听服务劫持。
- CA 路径当前为 `/home/three/.config/risk-aware-smolagents/pki/ca.crt`。
  当前 SHA-256 为 `97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`。
- 当前 CA 为 CA:TRUE，有效期 2026-11-17 至 2036-11-14；当前本地 server.crt 的
  CN/SAN 为 localhost、DNS:localhost/IP:127.0.0.1。离线 OpenSSL 链与 hostname 校验通过。
- SSLContext 使用 create_default_context(cafile=...)，check_hostname=True、CERT_REQUIRED。
  HTTPTransport 保留传入 Context 对象。没有放松证书验证、换 HTTP 或忽略环境 CA。

| 比较项 | 历史 B2 Day 5 / 后续成功证据 | 本次 Smoke |
| --- | --- | --- |
| 库版本 | B2 报告：OpenAI 3.52.0 / httpx2 2.16.2 | 当前版本相同；Smoke不调用SDK |
| Endpoint / hostname | localhost:8000/8001，HTTPS，校验通过 | 逻辑 URL 相同，TLS未完成 |
| connect/TLS 预算 | B2 冻结 remote_model.yaml 为30秒 | 入口显式覆盖为5秒 |
| read 操作预算 | 历史Client默认120秒 | 本次为10秒 |
| TLS 工厂 | 历史提交中的 _ssl_context / _http_client | AST 对比当前相应函数相同，但Smoke自行构造Client |
| Worker-0 TLS预检总耗时 | B2 raw remote_preflight：4.72192280399031秒 | 本次握手5.137秒后失败 |
| 后续 T1-1024成功 | raw preflight：Worker-0为0.47349130595102906秒 | 不能代表本次服务状态 |
| CA 绑定 | 后续T1证据记录上述同一CA哈希 | Smoke manifest未记录CA内容哈希/实际路径 |

5秒预算相对历史30秒明显更短，历史成功样本的 TCP+TLS 总延迟也曾接近5秒；这是
预算可能偏紧的证据，不是“延长一定成功”的证明。历史总延迟也不能精确等同于
纯 TLS 握手时长。

Smoke 从当时环境变量读取 CA。当前 CA 与既有成功证据相符，但失败 Run 没有保存
CA 哈希或对端证书，故不能用密码学证据确认当时加载的 CA 字节/远端证书始终相同。
本地 server.crt 的有效性不代表本次已看到远端实际证书。没有理由直接替换 CA。

## 7. 故障树

| 层 | 支持证据 | 反对证据 | 缺失证据 | 当前置信度 | 下一次最小验证方法（均未执行） |
| --- | --- | --- | --- | --- | --- |
| A 本地SSH进程管理 | 父进程/调度影响原则上可能 | 失败前后均存活，退出仅在cleanup后，stderr空 | CPU调度与系统事件 | LOW，未支持提前退出根因 | 保留同一Popen句柄和请求时序记录 |
| B 本地TCP监听/转发 | LISTEN仅是本地半段；主动探测有副作用 | 本地可连接，进入SSL层；映射正确 | GET实际peer/地址族、SSH通道打开时刻 | LOW至MEDIUM，转发数据路径未排除 | connect_tcp完成trace和socket peer，SSH通道debug时间戳 |
| C SSH远程目标端口 | 本地接受不证明远端端口及时可达 | 目标映射与历史正确，未见拒绝连接stderr | 远端connect/open确认、端口当时状态 | POSSIBLE，未确定 | 同一条SSH的direct-tcpip打开/失败事件 |
| D 远端HTTPS响应 | SSL握手一直未在预算内完成，与迟缓/未响应一致 | 历史成功但非同期对照 | 同期服务日志、实际TLS返回阶段 | 主要候选区域，MEDIUM | 单请求start_tls边界trace；仍不明确再单独授权远端只读对照 |
| E TLS握手/证书 | 握手timeout消息确定 | 没有证书验证错误，当前CA/本地链有效 | ClientHello/ServerHello/证书wire阶段 | 握手超时CONFIRMED；证书根因LOW | 记录严格TLS的SNI、CA哈希、handshake结果，失败细阶段仍需额外证据 |
| F HTTPClient/Proxy | 5秒预算较短，环境有代理 | trust_env=False、直连Transport、空池、单HTTP请求、0重试 | 历史依赖文件/环境完整快照 | 预算影响MEDIUM；代理/旧池LOW | 保持配置，增加安全trace验证实际超时和路由 |
| G Worker资源/服务负载 | 服务调度迟缓能解释症状 | 本次无资源/服务异常证据；GET不需要推理 | 同期Worker/CPU/event-loop指标 | UNKNOWN | 本次不扩展GPU审计；必要时另批极小服务只读对照 |

这棵树只描述可解释性；MEDIUM/POSSIBLE 不代表已证明因果，也不是训练标签。

## 8. 最可能原因及置信度

1. **CONFIRMED**：客户端 TLS handshake 在5秒connect预算内未完成，产生了这次
   ConnectTimeout；故障不在收到HTTP响应之后。
2. **MEDIUM / LIKELY problem region**：SSH转发后的端到端TLS响应/调度延迟超过该预算，
   或通道/目标服务未及时传回握手数据。不能在C/D/E之间进一步锁定。
3. **MEDIUM候选影响因素**：本次5秒预算相对既有30秒更敏感。
4. **LOW/POSSIBLE候选影响因素**：主动TCP健康观测引入额外连接；没有负载因果证据。
5. **不支持的已确认根因声明**：代理劫持、CA错误、Worker崩溃、SSH提前退出、端口串线。

最终结论仅 `PARTIALLY_IDENTIFIED`，不使用 `ROOT_CAUSE_IDENTIFIED`。

## 9. 下一次最小验证方案（待新授权）

建议不是重跑90秒Smoke，而是**新Run ID、仅Worker-0、一个GET的阶段化诊断**：

1. 复用 ConnectionLifecycle 的单个 SSHProcess/add/监测接口；不启动Worker-1，不改
   远端服务、不运行Agent或Completion、不重用失败Run。
2. 保持同一URL、localhost SNI、严格CA验证、connect=5/read=10和retries=0，
   不同时调整超时、CA、代理或转发目标。
3. 唯一新增变量为诊断观测：使用已安装 httpcore2 的 request.extensions["trace"]，
   白名单记录 connection.connect_tcp.started/complete/failed、start_tls.started/complete/failed
   和HTTP发送/接收边界的UTC/单调时间。记录底层异常的模块限定名和脱敏traceback。
   connect_tcp完成时从stream.get_extra_info("server_addr")获取实际peer；记录SNI、
   CA哈希、TLSContext验证flags和协议协商结果（若成功）。
4. 同步记录该owned SSH子进程的通道诊断（如新进程的-v日志及时间戳）。禁止记录
   Authorization、原始请求headers、密钥、敏感环境变量；trace info必须白名单化，
   不能把完整request对象直接序列化。
5. 健康观测保持原语义并计数其主动TCP尝试，不在同次验证中另换被动观测算法。
   只有一次GET尝试，任何失败后立即结束并清理，无重连/重发。
6. 若只有start_tls开始/失败而无完成，仍不能声称具体ServerHello阶段或CA故障。
   此时停止；由用户决定下一次单变量对照：恢复历史30秒connect预算，或另行批准
   一次远端loopback严格TLS只读对照。两者不自动执行，也不在同一Run内连续尝试。

最少待测量：本地TCP连接是否完成及peer、SSH目标通道打开耗时、TLS开始/结束边界。
只有新增明确授权后才创建隧道或发出任何网络请求。

## 10. 离线验证与未解决问题

执行的验证：

```text
.venv/bin/python -m pytest -q tests/test_connection_smoke.py tests/test_connection_lifecycle.py --disable-warnings
git diff --check
```

- 定向测试：32 passed in 3.46s，全部Fake Adapter/MockTransport，无真实网络。
- 临时内存断言：Client直连路由/空池/重试/TLS对象身份/timeout字典均通过；
  FakeSocket测试区分了握手超时与证书验证异常的映射，没有创建真实socket连接。
- OpenSSL离线验证当前本地CA/服务器证书链通过。
- 没有修改代码，因此未重复344项全仓回归或Checkpoint/协议测试；compileall不适用
  于本次仅新增Markdown的变更。
- 仍缺：真实TLS细阶段、实际socket peer、远端通道/服务响应时序、历史CA绑定和负载信息。

## 11. Git 状态

开始时HEAD及跟踪引用如第2节所列，工作区干净；仅新增本脱敏报告，代码、冻结配置、
证书和历史Run均不变。按本轮“仅本地离线”边界，不执行远端fetch/push。
远端分支状态只能依据本地origin/main跟踪引用，不声称联网重新核验。
本报告按仓库交付授权创建本地文档提交；推送留待另行允许网络交付时执行。

## 12. 资源使用与停止状态

SSH远程连接、Tunnel、HTTP/HTTPS请求、GET模型列表、Qwen调用、GPU操作、vLLM操作、
远端写入和Docker：本轮全部为0。只读取本地文件、安装依赖源码、证书公有部分及
代理变量的存在状态；没有读取或输出API Key、私钥或代理URL。

本轮结束后停止。历史Smoke仍BLOCKED；不进入Smoke、完整五任务Gate或P2-D3。
