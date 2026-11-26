# P2-D2 Worker 0 Single-Request TLS Diagnostic — Stage A

## Objectives

离线准备单 Worker、单 Tunnel、最多一个 GET `/v1/models` 的阶段化诊断。
本轮只实现及测试，不执行 Stage B，不 Commit，不 Push。

最终诊断 Gate：`TLS_DIAGNOSTIC_INCONCLUSIVE`（尚未执行真实请求）。
Stage A 工程检查通过；该状态不代表 TLS、90秒Smoke或五任务Gate成功。

## Git 与前置证据

- HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`，main。
- 本地 origin/main：`12e4792e2af516636aacd756056e7dbc5e7a41aa`。
- 开始时工作区干净；HEAD比本地origin/main多一个已有离线报告提交。
- 已读取 TLS Forensic Audit 及原始失败 Run；原始 Artifact Manifest SHA-256：
  `af3ae0b5dd8d0f2f9fbec9518ffeb43e85663e2e43950acd8a9bc57a3e9eae1b`。
- 原始7项产物哈希及两份冻结配置均匹配，历史记录未修改。
- 前次失败为 TLS handshake timeout，5.136730799秒，HTTP状态null；不能把它写成
  证书错误、SSH进程提前退出或已确认的远端服务故障。

## 实际客户端审计

| 组件 | 当前版本与路径含义 |
| --- | --- |
| OpenAI SDK | 3.52.0；其本地实现导入httpx2，本次GET不经过SDK模型封装 |
| HTTPX | 实际安装包为httpx2 2.16.2，而非名为httpx的包 |
| HTTPCore | 实际安装包为httpcore2 2.16.2 |
| smolagents | 1.26.0 |
| OpenAIServerModel | 当前smolagents/models.py；此诊断不实例化、不调用Agent或Completion |

新入口使用与失败Smoke相同的直接 httpx2.Client / HTTPTransport GET 路径：
显式 strict SSLContext、Client和Transport均trust_env=False、Transport retries=0、
follow_redirects=False、Timeout(10,connect=5)。connect=5、read/write/pool各10秒。
没有改用curl、其他HTTP库或模型调用包装器，也没有扩大超时。

CA路径和SHA-256、两份冻结配置哈希均在未来Stage B启动前验证，不匹配就停止。
API Key仍从原有环境变量读取；代码及报告不保存其值。

## 实现范围

新增独立入口 `scripts/run_p2_d2_worker0_tls_diagnostic.py` 和专项测试；未修改
生产ConnectionLifecycle、SSHProcess、RecordingModel、Agent、模型配置或Checkpoint代码。

### TCP/TLS Trace

- 利用当前真实 httpx2 请求的 extensions={"trace": callback}，由安装的
  httpcore2 Trace 实现触发 connect_tcp / start_tls 的 started/complete/failed。
- 同时记录HTTP发送/接收边界。回调不复制原始info字典、request对象或headers；
  只允许事件时间、host/port/timeout、SNI、验证flags、socket地址、状态和异常字段。
- 所有记录共享UTC、monotonic_ns、统一Run相对时基、请求相对耗时和Request ID。
- connect_tcp.complete从返回的真实stream读取server_addr和client_addr，TLS完成
  时读取可取得的协议/cipher/ALPN。无数据时保留unavailable，不从预期地址补造。
- failed回调立即复制异常链与脱敏traceback。离线测试发现连接池后续raise可能丢失
  底层cause，因此证书错误判断同时查阅早期Trace快照，不只看顶层ConnectError。
- Trace日志是同步回调的本地观察时刻，fsync等观测开销可能扰动总体耗时；它不等于
  wire-level TLS record时间，也不提高任何既定超时值。

### SSH进程和Channel

- 独立DiagnosticSSH适配器复用既有ConnectionLifecycle.add/monitor/close流程，
  仅拥有worker-0一个Popen句柄，不使用要求两个Worker的launch()。
- 保留已批准的-F /dev/null；新增-vvv诊断日志，一个-L 127.0.0.1:8000:127.0.0.1:8000。
  严格known_hosts、BatchMode、IdentitiesOnly、禁止密码回退、ExitOnForwardFailure，
  ControlMaster=no。没有ClearAllForwardings=yes，也不创建8001转发。
- 记录PID、/proc启动tick、实际cmdline、Run/Worker/Tunnel身份。清理活跃进程前
  核验启动tick、命令行及预期身份；只终止本次持有的子进程。
- stderr管道由独立reader持续排空至队列，writer异步脱敏/fsync；writer失败时
  reader仍继续排空，主流程观察错误后停止。不会因为慢日志磁盘直接堵塞SSH管道。
- SSH事件的monotonic时间在reader收到完整行时捕获，注明stderr_reader_receipt。
  它是接收观察时间，不是远端生成时刻或内核精确发送时刻。
- 解析认证完成、监听ready、forward请求目的地、direct-tcpip channel ID、send open、
  open confirm/open failed与close。目的地与channel的绑定注明“之前的单转发日志关联”，
  不谎称原始日志每一行均包含全部字段。
- OpenSSH debug不能证明TLS有效载荷已送达远端服务；Data Forwarding细节无直接证据
  时明确记录unavailable。SSH连接活着也不等于HTTPS正常。

### 三种地址严格分开

| 地址 | 未来真实数据来源 | 不可得时处理 |
| --- | --- | --- |
| HTTPS Client TCP peer/client local tuple | 实际HTTPCore stream的server_addr/client_addr | unavailable |
| SSH控制连接的peer和local tuple | 自有PID的socket FD inode与/proc/net/tcp关联 | unavailable，不使用目标配置值替代 |
| direct-tcpip destination与channel | 带接收时间的SSH debug日志 | unavailable或注明单转发日志关联 |
| 远端HTTPS服务实际TCP peer | 这次本地诊断无法直接观测 | 固定unavailable，不与本地8000 peer混淆 |

诊断适配器使用**被动/proc监听观察**，避免旧Smoke的主动connect_ex健康探测生成
额外direct-tcpip通道。它不会改变HTTPX/CA/超时，但确实改变了观察器流量；因此不能
把未来结果当作与旧Smoke监测流量完全相同的受控重复实验。本报告显式披露此差异。

## 离线验收

新增15个收集测试，全部不访问服务器：

| 测试/能力 | 结果 |
| --- | --- |
| 真正HTTPX/HTTPCore Trace Extension触发；Fake网络后端 | TCP与TLS started/complete按序记录；不是手工伪造Trace事件 |
| TCP超时 | connect_tcp.failed，无complete，不发送HTTP，不重试 |
| TLS超时 | TCP complete、TLS failed，无TLS complete，不误记为证书错误 |
| 明确证书错误 | 早期Trace保留SSLCertVerificationError，分类D |
| HTTP读取超时 | TLS complete后HTTP阶段失败，分类C |
| 时间戳 | HTTP Trace序列monotonic_ns严格递增，所有时基为同一进程时钟 |
| Client参数 | SSL严格验证、SNI localhost、connect5/read10、无代理mount、0重试 |
| Secret隔离 | Authorization/私钥字段不从Trace info进入日志，Bearer文本脱敏 |
| SSH排空 | 本地Python子进程输出超过pipe buffer；人为阻塞writer，reader仍排空、子进程能结束 |
| Channel解析 | 模拟日志关联forward目标、channel ID、open confirm与close |
| 被动Peer解析 | Fake/proc表按自有inode解码实际peer/local tuple，排除其他进程socket |
| 资源所有权 | 假SSH子进程仅一个8000转发；启动tick变化时拒绝清理 |
| 部分启动审计失败 | 仅释放刚创建并由Popen持有的假子进程，不遗留资源 |
| 已知连接错误 | 在提交GET前就停止，不补发请求 |
| Stage B授权/CA门禁 | 无授权或CA身份不匹配时，启动任何SSH之前拒绝 |

Fake backend仅替代网络Socket/TLS，事件由实际安装的HTTPX/HTTPCore调用链发出。
这些测试验证审计接线与分类逻辑，不是实际TCP/TLS连通性证据。真实socket peer、
真实SSH时序和TLS耗时均尚未采集。

## Commands and Tests

```text
.venv/bin/python -m pytest -q tests/test_worker0_tls_diagnostic.py
.venv/bin/python -m pytest -q --disable-warnings
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

- 专项测试：15 passed in 0.96s。
- 最终全仓回归：359 passed in 38.84s，无跳过；基线344，新增15项。
- compileall、diff check：通过。
- 历史7项产物、Manifest本身及冻结配置：匹配。

## Stage B 准备与限制

只有用户明确说出 `授权执行 P2-D2 Worker 0 Single-Request TLS Diagnostic` 后，
才可调用入口的 --authorize-stage-b，并使用新的唯一Run ID。该CLI参数本身不替代用户授权。

未来Run根目录为 runs/phase2_p2_d2_tls_diagnostic/，保存：run_manifest、
ssh_process_audit、ssh_channel_trace、tcp_tls_trace、worker_probe_audit、
connection_audit、resource_release、gate_summary及Artifact Manifest。
真实报告 p2_d2_worker0_tls_diagnostic_report.md 留待Stage B，不提前制造结果。

请求最多一次；不访问Worker 1。任何SSH/channel/TLS/HTTP/审计错误都停止，无自动或
手动重试、重连、Failover。已在途的HTTP线程可能需要有限超时结束后才能完成join；
这不会触发第二个请求，但不能把“检测错误时刻”与“所有资源完成关闭时刻”混为一谈。

成功请求只有在TCP peer、SSH控制peer、认证、channel open confirm和TLS complete
均有证据且资源清理成功时，才可判TLS_DIAGNOSTIC_PASS；缺少关键观测则为
TLS_DIAGNOSTIC_INCONCLUSIVE，失败为TLS_DIAGNOSTIC_BLOCKED。

现有Worker PID和GPU映射不能从本地SSH进程或HTTP响应推断。Stage B尚需按授权边界
复核已有Worker身份资料；入口不会私自追加GPU查询、远端shell命令或改变服务。
清理报告会保留“远端Worker状态未另行查询/unavailable”，不声称已经实测PID不变。

## Files Changed

- scripts/run_p2_d2_worker0_tls_diagnostic.py
- tests/test_worker0_tls_diagnostic.py
- 本Stage A报告。

生产代码、冻结配置、历史Run及此前报告均未修改；新文件保持未提交。
本轮未Commit、未Push、未fetch。

## Resource Use and Stop

真实SSH连接、Tunnel、HTTP/HTTPS请求、Qwen、Agent、Worker 1、GPU/vLLM操作：全部0。
只有本地元数据/代码读取和Fake测试，以及一个输出合成stderr的本地Python子进程；
该测试子进程已退出，没有残留。

Stage A到此停止。等待用户单独批准Stage B，不自动进入真实诊断、90秒Smoke或五任务Gate。
