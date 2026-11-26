# P2-D2 SSH Evidence Transport — Stage B

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_ssh_evidence_transport_20261125_001`。
最终 Gate：**SSH_EVIDENCE_TRANSPORT_STAGE_B_BLOCKED**。

停在真实连接之前：真实SSH连接尝试=0，连接型SSH Client=0，Remote Command=0。
没有TLS、OpenSSL、HTTP、Qwen、Worker/GPU/Docker或远端操作，没有自动重试。

原因是冻结适配器不能满足本次要求的非零退出验收：即使明确收到有效的远端23收据，
当SSH本地返回23时，exit_semantics仍输出FAILED_OR_INCOMPLETE，而本次要求成功传输。
本轮已保存纯离线最小复现，没有修改核心代码或通过重新解释返回值改判PASS。

这是上一轮适配器的验收覆盖遗漏：原测试覆盖SSH=0配远端非零，以及SSH=255的歧义，
未覆盖SSH与远端均为23时的成功传输语义。531项既有测试通过不意味着该缺口消失。
依据附件“发现实现无法满足真实传输验收，停止并报告，不临时重构”，未消耗真实连接。
Stage A历史报告/结果保持不变。

## 2. 冻结实现核验与直接复现

已读取Stage A日报、ssh_evidence_transport.py、tls_evidence_capture.py及专项测试。
Stage A gate_summary.json中的五份源码/测试/报告SHA-256均与当前文件匹配。

核心代码位置：
src/risk_agent/collection/ssh_evidence_transport.py:380–399。

```python
elif ssh_code == 255:
    transport = 'SSH_255_AMBIGUOUS'
elif observed and ssh_code == 0:
    transport = 'COMPLETED_WITH_REMOTE_RECEIPT'
else:
    transport = 'FAILED_OR_INCOMPLETE'
```

本轮只调用原函数进行离线复现：
ssh_code=23、local_timeout=false、合成receipt.returncode=23、evidence_valid=true。
结果：

| 字段 | 原函数离线输出 |
| --- | --- |
| ssh_process_return_code | 23 |
| remote_exit_status | 23 |
| remote_exit_status_observed | true |
| ssh_transport_status | FAILED_OR_INCOMPLETE |
| remote_termination_status | EXITED |

以上全部为**合成输入的离线函数结果**，不是本次真实SSH状态。
独立文件offline_exit_semantics_reproduction.json明确synthetic=true、真实连接0。
不能将这些23或EXITED填入本次运行状态。

## 3. Payload与帧协议接口差异

原适配器通过SSH stdout保存完整wire，再使用FrameDecoder把带Run ID、nonce、
sequence、SHA和终结收据的帧恢复成remote_stdout.raw和remote_stderr.raw。
SSH stderr原样保存为ssh_stderr_unclassified.raw，客户端日志走-E。

本次指定脚本若直接向SSH stdout输出SSH-EVIDENCE-V1前缀，原FrameDecoder会立即拒绝：
EvidenceError: Unframed remote stdout; provenance unknown。
offline_payload_protocol_reproduction.json保留这一离线结果。

这不证明OpenSSH无法传输bytes，而是指出“固定裸输出”和现有“带帧wire”的接口差异。
使用已有frame机制时，应明确区分原始SSH wire与保持不变的内层Payload，并确定如何
在不污染两流、不伪造退出状态的前提下满足SSH=23。不能临时绕过解码器，或把framed
wire直接当作55字节Payload。本轮没有改变Payload、追加收据字节、替换协议或另写采集器。

## 4. Git状态

- 分支main；HEAD：bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。
- 工作区有此前阶段未跟踪文件，原样保留，未执行reset/checkout/stash/commit。
- 记录所有既有未跟踪文件哈希；核心适配器、采集模块、TLS分类器、fixture、冻结配置
  和Stage A报告/测试不变。
- 只新增本次本地前置审计Run和本日报，未Commit、Push、fetch。

## 5. SSH身份配置与实际argv

执行的SSH命令只有本地ssh -V、原Alias的ssh -G，以及最终参数的-G展开。
这些是静态进程，不建立网络连接，不计为一次真实连接成功。

配置确认：risk-agent-remote→root@121.48.161.243:4035；专用IdentityFile为
/home/three/.ssh/risk_agent_audit，UserKnownHostsFile为/home/three/.ssh/known_hosts。
公钥指纹SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A与基准相符，
私钥mode=0600，没有读取或输出私钥内容。实际远端host key/容器/uid本轮未认证。

计划真实argv已保存在local_preflight.json，**没有执行**：

```text
/usr/bin/ssh -T -v -E <本次Run>/local_config_expansion.log.raw
  -i /home/three/.ssh/risk_agent_audit -p 4035
  -o UserKnownHostsFile=/home/three/.ssh/known_hosts
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
  -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectionAttempts=1
  -o ConnectTimeout=10 -o ControlMaster=no -o ControlPath=none
  risk-agent-remote "python3 -B -"
```

- 使用argv数组，无shell=True、无-F /dev/null、无ControlMaster复用。
- 最终-G无LocalForward/RemoteForward/DynamicForward、ProxyCommand或ProxyJump。
- log文件是本次**本地配置展开**日志，存在并持久化；不是连接型SSH Client Log。
- ConnectTimeout=10、适配器外部默认40秒，与本次建议15/30秒不同。已明确记录差异，
  未静默更改；本次没有实际应用任何连接期限。该差异不是主要阻塞的替代解释。

## 6. 远端身份与脚本

预期容器36f825bcc5da、uid=0；本轮实际均NOT_OBSERVED。没有额外SSH去获取身份。
planned_remote_payload_not_executed.py保存了待审核脚本，已经本地compile，未执行：
先用os.uname/getuid核验身份，不符退出90且不写Payload；相符时通过两个buffer
直接write/flush指定bytes，最后退出23。没有print、shell printf、远端文件或环境修改。

保存脚本仅是可审核的本地计划产物，不代表它已被发送到服务器。

## 7. stdout、stderr与非UTF-8预期

| 文件 | 角色 | 大小 |
| --- | --- | --- |
| expected_stdout.bin | 用户指定的本地预期bytes | 55 bytes |
| expected_stderr.bin | 用户指定的本地预期bytes | 14 bytes |
| remote_stdout.raw | 本次接收流 | 未创建 |
| remote_stderr.raw | 本次接收流 | 未创建 |
| ssh_stderr_unclassified.raw | 本次连接型SSH stderr | 未创建 |
| ssh_client.log.raw | 本次连接型SSH日志 | 未创建 |

预期stdout严格为固定身份前缀加005354444f55543aff804142430d0a，预期stderr严格为
005354444552523afe814445460a。各自SHA-256保存于local_preflight.json并纳入Manifest。
本地预期文件保留NUL、ff/80和fe/81等非UTF-8字节，但**没有真实往返比较结果**。
stdout_byte_equality、stderr_byte_equality、non_utf8_roundtrip_verified均为null。
不以预期文件的正确哈希冒充接收哈希，也不生成空接收文件。

## 8. 实际退出状态、生命周期和时间线

exit_status_audit.json保存本次实际状态：

- ssh_process_return_code=null。
- remote_exit_status=null，remote_exit_status_observed=false。
- ssh_transport_status=NOT_ATTEMPTED_PRECONDITION_BLOCKED。
- local_timeout=null，remote_termination_status=UNKNOWN。

本次没有连接型SSH PID/start/end/duration、终止信号或wait/reap收据；不是退出0。
没有真实跨流时间线或chunk接收记录，故未生成combined_observed_timeline.jsonl。
不能把离线函数的合成23收据、Fake测试的时间线或-G返回码当成本次远端结果。

## 9. 资源与Manifest预检

新Run：
`runs/phase2_p2_d2_ssh_evidence_transport/p2_d2_ssh_evidence_transport_20261125_001/`。

本地Run独占创建；预期bytes文件、配置展开原始输出和审计JSON均写入/fsync并读回。
磁盘空间及目录可写检查通过，原始空间数值保存在local_preflight.json。
Stage A Manifest、启动前预留及失败零启动能力由现有专项回归重新验证；本轮未为了
绕过解码器去初始化真实会话工厂或伪造其capture_prepared收据。

Manifest只覆盖本次存在的本地计划/审计文件，包含路径、大小、SHA-256、Run ID和
ctime观察值；ctime不冒充精确文件birth time。采用本地pending、fsync、无覆盖链接
发布，之后重新逐项验证。Manifest不包含自身，也不列不存在的接收流文件。

历史82份文件及八份Manifest均在本轮前核对，结束后哈希复核不变。
所有本任务产物均为未连接的前置审计，非真实SSH验收或正式数据。

## 10. 回归、编译及敏感信息检查

全仓 **531 passed in 41.15s，0 failed，0 skipped**，包含Stage A SSH专项53项及TLS
Evidence Capture、Classifier等相关回归。它们不包含本次23/23“应成功运输”的判据，
因此通过不否定上述静态/离线复现的缺陷。未修改或新增核心测试来掩盖该差异。

```text
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_transport/p2_d2_ssh_evidence_transport_20261125_001/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

编译及diff检查通过，任务产物敏感信息扫描通过。实际凭据只在内存比较是否意外
出现在文件中，不输出或记录值/摘要，未发现真实Key、私钥或证书材料。
全仓测试禁用真实网络/SSH/OpenSSL启动；urllib3导入时的1次IPv6 bind能力探测被
系统调用前阻止。测试中的Fake/Mock、本地Python假子进程均不是本次真实连接。

## 11. 清理与影响

未创建连接型SSH、远端测试进程、Tunnel或监听端口，没有本次诊断资源需要信号清理。
没有远端进程管理或pkill，没有访问Worker 0/1、GPU、Docker或其他用户资源。
远端终止状态没有实测收据，保持UNKNOWN；不追加SSH确认。
本地静态检查进程和测试进程已结束，没有覆盖历史Run或清理历史ControlMaster。

## 12. 失败解释与后续建议

直接观测：原源码分支、合成23/23输入的原函数输出、裸Payload被原解码器拒绝、
静态配置通过及既有测试通过。判断：这些接口/语义无法在不改核心或验收约定的
前提下满足本次目标，应在连接前阻塞。

无法确认：当前服务器/SSH服务是否可达、Payload真实传输效果、远端当前身份、
真实-E错误分流和远端退出状态。**本轮不是SSH连接失败，也未提供新网络根因证据。**

下一步需要独立离线修复/验收：补充有效收据下SSH=23且远端=23的成功传输判据及
不匹配、255、断链、无收据等反例；明确本次固定字节测试使用裸流还是已验收帧机制，
保持内层Payload逐字节不变且不以SSH码代替远端收据；同时明确10/40与建议15/30的
期限选择。该修复未在本轮实施，也不改变历史Stage A结果；之后才能重新审核唯一
真实连接的前置条件。

## 13. 六项最终回答

1. **真实OpenSSH是否完整传输stdout？未验证**，真实连接0次。
2. **真实OpenSSH是否完整传输stderr？未验证**，未创建接收流。
3. **连接型SSH Client Log是否独立保存？未验证**；仅本地-G日志存在，不冒充会话日志。
4. **非UTF-8 bytes是否真实往返一致？未验证**；仅保存并验证了本地预期bytes。
5. **Remote Exit Status是否正确识别？本次实际为null**；合成23/23暴露运输状态映射
   缺口，不能当作真实远端23。
6. **是否具备进入TLS Stage B条件？尚不具备**；本次真实传输验收未通过。

最终 **SSH_EVIDENCE_TRANSPORT_STAGE_B_BLOCKED**，停止等待审核。
不进入TLS Stage B、Isolation、Smoke、完整Gate、P2-D3或正式采集；不Commit/Push。
