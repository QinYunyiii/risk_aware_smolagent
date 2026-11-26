# P2-D2 Connection Smoke — Stage B / Final Real Two-Worker Verification

日期：2026-11-26（Asia/Shanghai）。
Run ID：`p2_d2_connection_smoke_20261126_002`。
最终 Gate：**CONNECTION_SMOKE_BLOCKED**。

## 1. 执行摘要

**本次已执行真实网络流程，并在Worker 0 SSH隧道就绪前置条件失败后停止。**
初始双Worker实时所有权检查通过，Worker 0隧道在冻结30秒就绪预算内未观测到本地监听。
两个HTTPS GET均为0，Worker 1实时复核和隧道创建均为0，未重试或切换Worker。

本次真实SSH Client进程共2个：1次成功的双Worker只读预检、1个未就绪的Worker 0隧道子进程。
远端只读预检退出0；隧道子进程在清理前仍存活，本地端口关闭，stderr为0 bytes。
随后按正式生命周期对本次拥有子进程terminate并wait/reap，退出码-15，PID已消失，端口已释放。
没有证据判断隧道具体卡在SSH握手、认证或其他阶段，根因仍为UNKNOWN。

按本轮HTTPS GET尚未开始、隧道前置条件未满足判断为BLOCKED；已发生的SSH网络操作单独计数，
不声称“真实网络操作为0”。原Runner的status=BLOCKED原样保留，不改写为PASS。
没有发出模型HTTP请求，因此不能判定两个Worker的HTTPS、鉴权或API Model ID已通过。

## 2. 唯一正式入口、授权及Git状态

使用本轮用户授权，正式命令只执行一次，无Fake/Monkeypatch工厂，无代码或配置修改：

```text
.venv/bin/python scripts/run_p2_d2_connection_smoke.py --authorize-remote --run-id p2_d2_connection_smoke_20261126_002
```

CLI退出码：**1**；完整stdout/stderr和调用开始/结束记录均保留。
固定002目录执行前不存在，没有覆盖、复用或自动换号。
开始：2026-11-26 02:18:49.643344295 +08:00；结束：02:19:40.774095914 +08:00。
两者是本地wall-clock记录，不当作单调总耗时。

HEAD：`bfd74dacf523d4677bc662e2e92b3bd5c8c09a10`；分支main。开始时四个既有Stage A修改文件和历史未跟踪代码、测试、文档
全部保留；本轮不修改其中任何一份。本轮只新增本日报、002 Run的追加审计和本地准备目录。
完整Git状态保存于baseline.json及最终状态文件。无Commit、Push、fetch、reset、clean或历史操作。

## 3. 前置验收和本地环境

已阅读Worker 1 Revalidation Stage A、Connection Smoke Stage A、TLS Stage B 004和历史BLOCKED报告，
并核对正式CLI、Worker Ownership Preflight、SSHProcess、HTTP Client/TLS和Manifest实际调用链。
最新Stage A Manifest的250个文件重新核验，正式三份实现及冻结配置SHA-256与成功Fake产物一致。
Worker 1复核确在Worker 0 PASS之后、其隧道创建前；HTTP预算和TLS/Key处理均与验收版本相同。

本地环境门禁全部通过：

- RISK_AGENT_API_KEY和RISK_AGENT_CA_BUNDLE已继承且可安全读取，只记录存在性。
- CA存在可读，有效期检查通过，SHA-256为
  `97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef`，匹配冻结值。
- SSLContext初始化成功，CERT_REQUIRED且check_hostname=true；没有放松验证或延长预算。
- 专用SSH密钥可读、权限私有，原可信公钥指纹匹配；known_hosts目标Host Key指纹匹配。
- 本地8000/8001通过/proc/net/tcp和tcp6被动检查无LISTEN，没有额外端口探测连接。
- Client/Transport trust_env=false、HTTP retries=0、follow_redirects=false。
- HTTP connect=5、read/write/pool各10秒保持不变；本次没有进入HTTP Client创建和请求阶段。
- 预检工具及正式CLI内部各执行一次本地ssh -G配置展开，不属于SSH远端连接尝试。

不打印、复制或哈希API Key，不记录Authorization Header或SSH私钥，不申请OpenAI云端Key。

## 4. 初始双Worker实时所有权与GPU映射

唯一初始只读SSH命令由正式CLI发起，使用专用key、严格known_hosts、BatchMode、
-F /dev/null、ClearAllForwardings=yes、ControlMaster=no、ControlPath=none、ConnectionAttempts=1。
它没有转发，未调用HTTP，只读取当前容器、/proc、既有部署状态及GPU 0/1的index/UUID。

SSH返回0，stderr为空；远端Metadata观测时间：2026-11-25T18:18:52.441529+00:00。
对应北京时间2026-11-26 02:18:52.441529。容器为36f825bcc5da、root、Ubuntu 22.04。

| 字段 | Worker 0 | Worker 1 |
| --- | --- | --- |
| 当前PID | 382420 | 382422 |
| Process start ticks | 519951147 | 519951148 |
| alive | true | true |
| 安全Command Identity | vllm-worker-0 | vllm-worker-1 |
| CUDA_VISIBLE_DEVICES | 0 | 1 |
| 当前Physical GPU | 0 | 1 |
| GPU UUID | GPU-12e028da-9b7a-d52b-f8b8-44221a0b0160 | GPU-aec607a6-84e2-0b80-eddd-996bf48c344d |
| Endpoint | 127.0.0.1:8000 | 127.0.0.1:8001 |
| Listener Ownership | true | true |
| 声明模型ID | Qwen/Qwen3-8B | Qwen/Qwen3-8B |
| Ownership Verified | true | true |

完整命令摘要：

- Worker 0：`a4acce03f6a6bf66db69fcf3fe9748f36ed82f076d28e8eb67104e415b8e1e34`。
- Worker 1：`e760e98391977e9ec021b8df414eade46e47a260603670f6d16aff8018941d1a`。

模型路径均为`/mnt/risk-aware-smolagents/remote_models/Qwen3-8B`；实际监听owner从当前/proc
发现，再与部署状态PID、GPU/UUID及cmdline交叉核验，没有直接将历史PID当作当前可信。
原始cmdline和environ不写入日志，Metadata只导出安全身份字段。
模型ID在这里来自进程启动参数，不能代替GET响应中的API模型身份验证。
未查询或操作GPU 2–7，未根据显存空闲启动任何服务。

冻结只读helper保留解析后的完整JSON字段与SSH退出记录，没有保留其stdout原始wire bytes；
本次不将重新编码的JSON宣称为逐字节SSH stdout证据。

## 5. Worker 0隧道与异常链

使用原SSHProcess，实际argv保存在resource_release.json。关键参数为：

```text
ssh -F /dev/null -N -T -i <dedicated-key> -p 4035
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
  -o UserKnownHostsFile=<known-hosts> -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=15
  -o ControlMaster=no -o ControlPath=none
  -L 127.0.0.1:8000:127.0.0.1:8000 root@121.48.161.243
```

-F /dev/null隔离历史转发；隧道没有使用会清除显式-L的ClearAllForwardings=yes。
没有RemoteForward、共享Master、后台-f或自动重连。

本次owned child PID：**1249034**；Tunnel ID：`dddb558897d44d5283cfbb128043fa4d`。
connection_created和cleanup_started均观察到PROCESS_ALIVE、LOCAL_PORT_CLOSED；
没有connection_ready事件，没有provider_request_started，也没有启动HTTP Client。

完整实际异常链仅一层：

```text
TimeoutError: Owned SSH forward readiness timeout
```

冻结生命周期设置30秒单调就绪deadline；SSH ConnectTimeout为15秒，HTTP connect/read预算未触发。
没有保存该阶段精确单调起止，不能把“冻结30秒预算触发”写成测得恰好30.000秒。
本地UTC created=18:19:12.631811、cleanup_started=18:19:40.692223，wall-clock差28.060412秒。
wall时间与单调预算不一致的原因未在本轮调查，不据此篡改单调超时逻辑或推断根因。

原Runner诊断字段为provider_read_timeout / PROVIDER_REQUEST_TIMEOUT。
源码diagnose按字符串包含read判断TimeoutError，readiness也命中该条件。
本次实际事件是**SSH forward就绪等待超时**，没有Provider HTTP读操作；保留原诊断及原文件，
在failure_interpretation.json明确记录语义限制，没有为通过验收而修Classifier或代码。

stderr文件真实存在且为0 bytes；没有可用SSH阶段日志证明超时发生的具体协议阶段。
不能因子进程存活推断SSH已认证或转发建立，也不能从此事件推断远端TLS证书错误。

## 6. Worker 0请求、Worker 1复核及请求

| 验收项 | Worker 0 | Worker 1 |
| --- | --- | --- |
| 初始所有权 | PASS | PASS |
| Tunnel child创建/尝试 | 1 | 0 |
| Tunnel READY | 未通过 | 未执行 |
| 追加实时复核 | 不适用 | 0次，未执行 |
| 初始与实时比较 | 不适用 | 无实时快照，不能比较 |
| GET /v1/models | 0 | 0 |
| TCP连接到HTTPS Endpoint | 未执行 | 未执行 |
| TLS握手 / 证书链 / hostname | 未执行 | 未执行 |
| HTTP Status / Response Body | null / 未收到 | null / 未收到 |
| API Key鉴权结果 | 未执行 | 未执行 |
| Actual API Model ID | 未收到 | 未收到 |
| Request Latency / Provider Request ID | null / null | null / null |

Worker 0未完整PASS，因此正式入口没有触发Worker 1复核、隧道或GET，门禁按预期停止。
没有复用Worker 1初始快照伪造实时复核，也没有复制历史Worker 0 TLS成功结果。
两个request/response补充文件明确NOT_STARTED/NOT_RECEIVED，不创建空response raw冒充响应。
TLS审计仅记录未执行状态；本地Context正确不等于两个Worker TLS已经通过。

## 7. 实际预算、SSH进程统计和停止条件

| 操作 | 本次实际 |
| --- | ---: |
| 正式CLI执行 | 1 |
| 初始只读SSH | 1，退出0 |
| Worker 1复核只读SSH | 0 |
| Worker 0隧道SSH child | 1，未就绪 |
| Worker 1隧道SSH child | 0 |
| SSH Client进程总数 | 2 |
| Worker 0 / Worker 1 GET | 0 / 0 |
| HTTP总数 / Worker TLS握手 | 0 / 0 |
| 自动重试 / Failover | 0 / 0 |
| Completion / Agent / Tool / Checkpoint / Restore | 全0 |
| GPU只读查询 | 1次，限定GPU 0/1 |
| GPU计算、vLLM启停、Docker、远端写入 | 全0 |
| 额外健康检查、HEAD、OPTIONS、执行后SSH探测 | 全0 |

本地生命周期内部仍按冻结实现用connect_ex检查转发端口；本次端口未监听，
没有由此发出额外HTTP或到Worker Endpoint的TLS请求。
只读SSH helper未导出本地PID；其返回及returncode由subprocess.run等待路径保证。
该PID标记NOT_RECORDED，没有猜测或伪造。owned Tunnel的PID及退出均有独立正式记录。

## 8. 受控资源释放

超时后finally只对本次Popen句柄对应的1249034执行terminate并wait，返回**-15（SIGTERM）**。
清理开始时该child仍存活，清理结束为PROCESS_EXITED；该退出码代表本次受控清理，
不写成自然退出0，也不据此反推超时根因。没有使用kill、pkill、GPU reset或关闭外部连接。

正式resource_release passed=true、manager_closed=true、owned_processes_exited=true、
local_ports_closed=true。随后本地被动复核/proc/1249034已不存在，8000/8001均无LISTEN。
未另开SSH检查远端运行后状态；无远端管理操作，初始观测时两个Worker均存活。
没有证据声称另行取得了远端执行后快照。

## 9. 离线工程回归

- 专项：**116 passed in 20.20s，0 failed，0 skipped**。
- 全仓：**776 passed in 63.95s，0 failed，0 skipped**。
- compileall、git diff --check及敏感材料扫描通过。
- 测试均为Fake/Mock，防护钩子拒绝真实网络及SSH/OpenSSL工具，不计入上述真实SSH次数。
- 依赖导入的一次本地IPv6 bind能力检查被阻止，原堆栈保留，没有建立监听或连接。

```text
.venv/bin/python runs/phase2_p2_d2_connection_smoke_preflight/p2_d2_connection_smoke_20261126_002/offline_test_guard.py -q tests/test_connection_smoke.py tests/test_connection_smoke_integration.py tests/test_connection_smoke_worker1_revalidation.py tests/test_connection_lifecycle.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_connection_smoke_preflight/p2_d2_connection_smoke_20261126_002/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

测试前后没有修改实现或Fixture。776通过验证工程回归，不将其解释为真实HTTPS可用。

## 10. Manifest、历史保护与产物

正式Run：`runs/phase2_p2_d2_connection_smoke/p2_d2_connection_smoke_20261126_002/`。
准备及原始CLI调用记录：`runs/phase2_p2_d2_connection_smoke_preflight/p2_d2_connection_smoke_20261126_002/`。

正式入口原始artifact_manifest.json的**9个条目**已按大小和SHA-256复核，连同原核心Gate、
资源记录、初始Metadata、三个连接事件及stderr原样保留。
追加的remote_preflight、worker_ownership_initial、worker1_ownership_revalidation、ssh_process_audit、
两个独立request/response状态、tls_audit、计数、失败解释、执行后释放检查、测试和日报，
由独立 **artifact_manifest_final_audit.json** 统一封存，包含原Manifest，排除自身。
不改写已发布的核心Manifest或已有原始Run文件；追加材料明确来源和未执行状态。

历史 **7331个文件**SHA-256保持不变；**133份历史Artifact Manifest**、
**4438个条目**均核验匹配，包括历史001 BLOCKED和最新Worker 1 Stage A。
所有本轮开始前的源码、配置、测试、文档均与baseline相同。
完整证据私有保存、fsync、SHA-256、独占pending/no-clobber原子发布，再次重读核验文件集合。
原始CLI stdout/stderr不截断；没有输出API Key/Authorization/私钥，没有自动上传实验raw。

## 11. 最终Gate及后续准入

**CONNECTION_SMOKE_BLOCKED**。初始两个Worker的进程身份和GPU归属已真实验证；
Worker 0 SSH forward未就绪，两个HTTPS GET未开始，目标HTTPS/API验收仍未完成。
**当前不具备进入P2-D2 Full Five-Task Gate的条件。**

本轮不增加额外诊断、健康检查或工程改造，不重试，不重启Worker，不自动进入Qwen Completion、
Full Gate、P2-D3或正式采集。没有Commit/Push。固定002原始记录保留，完成后停止等待用户审核。
