# P2-D2 TLS Evidence Capture — Stage B

## 1. 执行摘要与 Gate

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_tls_evidence_capture_stage_b_20261125_001`。
最终 Gate：**TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**。

本次停在本地前置审计：真实SSH连接=0、Remote Command=0、OpenSSL握手=0。
**阻塞是Stage A采集模块与远端SSH之间尚未接线，不能保证保存完整的原始两流证据，
不是远端服务失败或SSH不可访问的诊断。**

附件要求“如果现有实现存在无法满足的要求，先报告，不要擅自重构”，并限制为
“只新增本次Run产物及日报”。因此本轮没有临时编写新的远端采集器/relay，也没有
运行旧的摘要诊断代替完整证据采集。Stage A原PASS保留，其范围是本地采集能力，
不等于真实SSH传输已经完成。

## 2. 已核实的实现及具体阻塞

已完整读取Stage A报告、Classifier Audit、原TLS Isolation Stage B报告，以及模块、
两个入口、Manifest、分类器和回收实现；结论来自实际代码：

| 位置 | 现有行为 | 对本次要求的影响 |
| --- | --- | --- |
| tls_evidence_capture.py:333 capture_process | 必须注入process_factory，仅管理调用者交付的OwnedProcess | 没有远端原始流传输工厂 |
| tls_evidence_capture.py:57 PopenPipeProcess | 包装当前主机上已有binary Popen两条pipe | 包装本地SSH得到的是SSH通道，不会自动变成远端OpenSSL两流 |
| tls_evidence_capture.py:492 offline_preflight | 明确stage_b_enabled=false，real_transport=not wired | 默认入口未实现远端采集 |
| scripts/test_p2_d2_tls_evidence_capture.py:13 | 只允许offline_preflight | 没有获验收的真实执行模式 |
| scripts/run_p2_d2_remote_local_tls_isolation.py:37 | 仍调用旧run_authorized | 未调用Evidence Capture模块 |
| remote_local_tls_isolation.py:75及后续解析 | 按行decode后只保存筛选summary | 原始bytes及未匹配输出不可恢复 |

Stage A报告第7、14节已经明确framing/relay只是下一阶段设计，未接线。
本轮又用源码搜索核对capture_process/EvidenceStore的使用处：只有模块内部和离线
测试，没有已验收的SSH framing接收器或远端无损流工厂。

当前可确认的能力：本地binary捕获、SHA-256、时间线、超时和本次句柄回收、原子
Manifest均通过离线测试。当前不能确认的关键要求：

1. 经SSH完整保存远端OpenSSL stdout/stderr，且保留来源。
2. 区分SSH客户端stderr、控制程序异常stderr及OpenSSL stderr。
3. 把SSH退出/外部超时与远端控制程序/OpenSSL退出/回收收据可靠关联。
4. 本地持久化失败或连接中断时停止远端握手，并取得本次OpenSSL回收证据。

把SSH整个stderr直接标成OpenSSL stderr，或者先运行旧入口再复制摘要为.raw，均
不能满足要求。只改.bin/.raw文件后缀也不能补足这个缺口。没有采用这些替代方式。

## 3. Git状态与本地前置检查

- 分支main；HEAD：bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。
- 工作区不干净：存在此前诊断任务的未跟踪文件。全部原样保留，哈希写入baseline。
  本轮未清理、暂存、Commit、Push或fetch。已知未提交文件不是上述技术阻塞的替代解释。
- Stage A报告、模块、离线入口、测试存在，四份文件均与Stage A gate_summary.json
  保存的SHA-256一致；没有仅凭口头PASS继续。
- 本地新Run目录创建成功；独占写入探针、fsync及读回SHA-256通过。
- 检查时磁盘空闲1,010,700,849,152 bytes，原始数值保存于local_preflight.json。
- Stage A合成样例的原始文件/时间线Manifest重新验证通过。

## 4. SSH配置本地展开

只执行本地ssh -G参数展开，没有连接服务器，也没有执行Remote Command。
完整结果保存于ssh_effective_config.txt；展开诊断保存于
ssh_config_expansion.stderr.txt，后者不是一次真实SSH连接日志。

已确认Alias risk-agent-remote有效参数：

- hostname=121.48.161.243、user=root、port=4035。
- IdentityFile=/home/three/.ssh/risk_agent_audit。
- 公钥指纹SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A，与可信基准一致。
- UserKnownHostsFile=/home/three/.ssh/known_hosts；私钥和known_hosts本地可读，未输出内容。
- BatchMode=yes、StrictHostKeyChecking=yes、IdentitiesOnly=yes、PasswordAuthentication=no。
- ClearAllForwardings=yes、ForwardAgent=no；无LocalForward/RemoteForward/DynamicForward。
- ControlMaster关闭，ControlPath=none；未启动后台连接。

配置展开通过不等于本次SSH认证成功。当前远端主机身份未重新观测。

## 5. 远端与Worker身份、CA、版本

| 项目 | 授权预期/可信历史基准 | 本次实际状态 |
| --- | --- | --- |
| 容器 | 36f825bcc5da | NOT_OBSERVED |
| 远端用户 | root | NOT_OBSERVED |
| Worker | worker-0，127.0.0.1:8000 | NOT_OBSERVED |
| 项目/模型身份 | Qwen/Qwen3-8B及项目所有权证据 | NOT_OBSERVED |
| Worker PID、启动参数、Listener | 必须重新核验 | NOT_OBSERVED |
| OpenSSL | /usr/bin/openssl，3.0.2 | NOT_OBSERVED |
| CA | /root/.config/risk-agent/tls/ca.crt | 本次未检查远端存在性/可读性 |
| CA SHA-256 | 97e07749f90c2d418a8dce22148c2e1fc77a14adbf913d6c74665e477c4b5bef | 本次未读取远端CA，未通过新的远端哈希门禁 |

不以之前的Worker PID、启动参数摘要或历史握手日志填充本次身份验证。
没有访问Worker 1或查询GPU，也没有HTTP模型身份请求。

## 6. 冻结OpenSSL argv与本次执行状态

以下是实际源码中已经冻结的**计划命令**，不是本次执行记录：

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost \
  -verify_hostname localhost -verify_return_error \
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

未改Connect Address、Port、SNI、CA、hostname/证书验证、state/msg或5秒超时。
本次没有启动OpenSSL，实际PID、Executable Path、start/end time、duration、returncode、
signal和wait/reap收据均为null/NOT_OBSERVED，不用0冒充成功退出。
没有为读取版本/help而额外连接远端。

## 7. TLS结果与事件时间线

TLS分类器源码保持不变。本轮没有新TLS证据供其消费，不制造分类器输出；
tls_classifier_result=null，Stage B Gate因前置失败为BLOCKED。

| 事件 | 本次结果 |
| --- | --- |
| ClientHello | NOT_OBSERVED |
| ServerHello | NOT_OBSERVED |
| Certificate | NOT_OBSERVED |
| Certificate Verification | NOT_OBSERVED |
| Server Finished | NOT_OBSERVED |
| Client Finished | NOT_OBSERVED |
| Handshake Complete | NOT_OBSERVED |
| TLS Alert | NOT_OBSERVED |
| Timeout | NOT_OBSERVED |

NOT_OBSERVED的原因是握手未执行，不是声称这些事件在已执行的握手中缺失。
没有新增Verification: OK或证书验证失败的证据。历史结果保持不确定，不重分类。

## 8. 原始文件与进程状态

stdout.raw、stderr.raw和真实SSH客户端日志均**未创建**。其大小、SHA-256及创建时间
在raw_artifact_status.json中为null。没有生成空raw文件假装已捕获空流，Manifest也
不列出不存在的文件。Stage A的.bin只是文件命名差异，真正阻塞是传输实现缺失。

SSH connection attempts=0；Remote Command attempts=0；TLS attempts=0；retry=0。
SSH退出码、Remote Command退出状态、OpenSSL退出码均未取得，不能由本地预检
命令返回0替代。这也是本轮选择BLOCKED而不是“完成但证据不充分”的INCONCLUSIVE原因。

## 9. Run产物与Manifest审计

新独立目录：
`runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_001/`。

包含：baseline.json、local_preflight.json、本地配置展开输出、fsync探针、离线测试
防护入口与test_results.json、tls_event_observations.json、raw_artifact_status.json、
resource_release.json、gate_summary.json和artifact_manifest.json。
这些是一次未执行真实握手的前置审计产物，不是新TLS成功样本或正式训练数据。

Manifest只覆盖本次实际存在的文件，记录大小、SHA-256、路径和本地创建观察时间，
排除自身避免自引用；写入fsync后重新读取逐项校验。创建观察时间注明为本地文件系统
ctime采样，不能当成精确birth time或远端进程时间。

历史Isolation、Classifier Audit、Evidence Capture Stage A共42份文件及五份Manifest
在操作前核对；结束后哈希再次比较。Stage A受验收文件、现有分类器、旧入口、
fixture和两份冻结配置不变；所有既有未跟踪文件内容也保持不变。

## 10. 相关离线测试与检查

最终：**166 passed in 4.00s，0 failed，0 skipped**，其中53项Evidence Capture专项，
113项分类器、Isolation、Worker 0诊断及连接生命周期/Smoke Mock回归。

```text
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_stage_b/p2_d2_tls_evidence_capture_stage_b_20261125_001/offline_test_guard.py -q tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py tests/test_worker0_tls_diagnostic.py tests/test_connection_lifecycle.py tests/test_connection_smoke.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src/risk_agent/collection/tls_evidence_capture.py src/risk_agent/collection/remote_local_tls_isolation.py scripts/test_p2_d2_tls_evidence_capture.py scripts/run_p2_d2_remote_local_tls_isolation.py tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py
git diff --check
```

编译和diff检查通过。测试防护入口禁止真实网络/SSH/OpenSSL；urllib3导入时一次
本地IPv6 bind能力检查被系统调用前阻止，没有连接、解析或发送数据。
测试中的Mock HTTP/SSH和本地Python假子进程不是本次真实网络实验。
没有运行完整项目测试，也没有执行真实Smoke。

## 11. 资源释放与影响范围

未创建诊断SSH/OpenSSL进程、Tunnel、后台进程或监听socket，因此没有本次遗留的
诊断资源需要清理，也没有向任何进程发送信号。
没有接触Worker 0/1、GPU或其他用户服务；Worker 0当前运行状态未观测，不伪造
“前后PID和运行状态相同”的实测证据。未尝试关闭既有本地监听或历史连接。

只新增本次Run产物及本日报，源码、配置、历史Run和远端均未修改。未Commit/Push。

## 12. 失败、推断边界与下一阶段

直接观测：本地配置和持久化功能通过；新入口仅离线；旧入口仍筛选输出；缺少已接线
的SSH raw framing/relay；测试通过。没有观察到SSH认证、TLS或远端服务失败。

有证据支持的判断：直接运行当前远端入口仍不能提供本次要求的完整raw证据，故必须
在SSH之前停止。不能推断远端服务已改变、Worker不可用或之前的SSH转发超时已解决。

下一项最小工作应是**独立的离线SSH framing/relay接线与验收**：复用Evidence Capture
的安全创建、chunk写入、fsync和Manifest；明确OpenSSL两流的无损帧及各层终结收据；
测试断流、丢帧、哈希不符、本地写入失败及远端本次子进程回收，再重新审核真实执行
前置条件。它不是再写一套采集器，也不应暗中塞入本次限定的真实执行任务。
本轮没有实施该接线，也没有自行扩大授权。

## 13. 四项最终回答

1. **TLS握手是否有充分证据证明完成？没有。**本次真实握手未执行。
2. **具体缺失什么？**本次远端身份/CA/Worker重新核验、OpenSSL原始两流和完整
   生命周期、完成事件及远端回收收据；先缺少能够可靠采集它们的已接线传输层。
3. **之前SSH Tunnel超时是否获得新的解释？没有。**本轮没有新增网络观测，历史根因
   保持未确证，不能把工程接线缺口当成当时网络故障根因。
4. **是否具备进入Connection Smoke Test的条件？尚不具备。**本次Stage B未通过，
   无新的严格TLS验收证据，也没有进入Smoke的授权。

最终 **TLS_EVIDENCE_CAPTURE_STAGE_B_BLOCKED**。停止等待审核；不进入Smoke、完整
五任务Gate、P2-D3或正式采集。
