# P2-D2 SSH Evidence Transport — Stage B / 20261125_002

## 1. 执行摘要

最终 Gate：**SSH_EVIDENCE_TRANSPORT_STAGE_B_PASS**。
日期：2026-11-25（Asia/Shanghai）。
Run ID：`p2_d2_ssh_evidence_transport_20261125_002`。

完成唯一一次真实OpenSSH连接和一个Remote Command，无重试/Failover。真实stdout
55字节、stderr14字节均与Compatibility Repair测试引用的权威常量逐字节一致，
包括NUL、非UTF-8、CRLF/LF。SSH退出23，V1原始终结收据确认受监督子命令实际退出23
且已wait/reap；适配器分类SUCCESS / COMPLETED_NONZERO，无超时。

独立客户端日志、完整Wire、未分类本地stderr、两条Payload、原始收据和观察时间线
均保留、fsync并校验SHA-256。原始Manifest通过，最终追加清单覆盖本次所有派生产物。
执行后全仓 **577 passed in 42.75s，0 failed，0 skipped**。

本轮没有OpenSSL、TLS、HTTP、Qwen、Agent、Worker/GPU/Docker操作或SSH转发。
传输PASS不代表TLS握手通过，不自动进入任何下一阶段。

## 2. Git、实现和历史保护

- 分支main；HEAD保持bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。
- 原有未跟踪修改保留。Stage A及Compatibility Repair源码、测试、fixture、TLS
  Classifier、冻结配置均与执行前哈希一致；132份历史文件及11份Manifest完整性通过。
- 严格使用现有prepare_byte_probe、FrameEncoder、FrameDecoder、capture_ssh_transport、
  PopenPipeProcess及Manifest机制；没有重写协议、修改核心或绕过Adapter采集SSH。
- 本轮只新增Run专属启动工厂/审计产物和本日报，不Commit、Push、fetch。
- 原docs/phase2/daily_reports/p2_d2_ssh_evidence_transport_stage_b_report.md属于001的
  BLOCKED历史报告，原样保留。本次另用带日期和002的文件名，避免覆盖历史报告哈希。

## 3. 本地预检与唯一执行保障

指定002目录执行前不存在；若已存在，启动器立即拒绝，不改ID、不覆盖、不重试。
预检在同Run ID的独立本地准备目录进行，真实启动前完整复制到002/local_preflight/。
本地ssh -V/-G只展开版本与配置，没有用于额外远端身份查询。

检查通过：磁盘空闲1,009,112,612,864 bytes；Run/日志/两流可写；关键文件及Manifest
pending在工厂调用前预建/fsync；测试常量和计划源码哈希固定。新启动工厂先用Fake
SSH验证异步stdin发送路径，随后265项相关离线回归通过，才执行真实连接。

真实工厂最多创建一次连接进程，执行claim独占落盘；重复调用会被拒绝。源程序通过
受控binary stdin线程发送，监督读取/超时控制同时生效，不在等待连接时同步阻塞工厂。
13,039字节源码全部发送完成，无写入异常；线程已结束。无API Key环境传给SSH/字节子命令。

本轮有本地-V/G静态检查进程，**连接型SSH Client只有1个**；没有第二次真实连接。

## 4. SSH身份与实际argv

Alias：risk-agent-remote；目标root@121.48.161.243:4035。
专用公钥指纹：SHA256:u51mgYnzoKDgEydhLKmI07XAOnCc2jcEhfc+BTOIL7A。
实际日志确认专用key被接受、publickey认证、Host Key匹配known_hosts、Exit status 23。
未输出或复制私钥/API Key。

实际执行参数（路径简写仅用于本报告，完整argv在run_manifest.json及process_result.json）：

```text
/usr/bin/ssh -T -v -E <Run002>/ssh_client.log.raw
  -i /home/three/.ssh/risk_agent_audit -p 4035
  -o UserKnownHostsFile=/home/three/.ssh/known_hosts
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
  -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectionAttempts=1
  -o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none
  risk-agent-remote "python3 -B -"
```

没有-F /dev/null、-L/-R/-D、ProxyCommand/ProxyJump或历史ControlMaster复用。

本次明确授权15秒Connect/30秒Overall，工厂仅把核心builder生成的ConnectTimeout=10
改为15，调用原API时显式指定local_timeout_seconds=30。未改builder源码或默认值。
capture_prepared保留原builder意图10秒，run_manifest分别记录builder_argv与实际argv；
实际Popen参数和process_result.argv均为15，执行前最终-G也确认15。该差异没有隐瞒。

## 5. Remote Command与容器身份

唯一SSH Remote Command为python3 -B -。发送的是现有prepare_byte_probe生成的原V1
源码；默认门禁要求hostname=36f825bcc5da、uid=0，失败则返回90且不启动Payload。
门禁通过后仅监督一个Python字节发射子进程，读取其两条binary PIPE，取得真实
wait收据后发送exit帧，再由外层返回23。没有额外SSH、远端目录/文件或服务操作。

远端Payload子进程实际executable为/usr/bin/python3.10（argv解释器/usr/bin/python3），
证明Python 3可用；没有另跑版本命令。outer/child均-B，不生成Python字节码文件。

身份判断的证据是：精确已审核源码及哈希、该源码在唯一认证连接内的门禁控制流、
匹配nonce的完整Payload/收据和最终退出23。没有独立hostname/whoami远端命令。
Payload身份前缀是固定字节，**不单独将它当作动态身份测量**；门禁控制流和完成证据
共同支持本次容器/uid匹配。

## 6. 三类原始证据与字节比较

预期值通过加载tests/test_ssh_evidence_compatibility.py所引用的
ssh.BYTE_PROBE_STDOUT/STDERR取得，并与ByteProbePlan返回bytes核对；未按长度猜测
或重新拼装。原始期望副本、测试源码哈希和计划源码保存在local_preflight/。

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| remote_stdout.raw | 55 | cb30b9a81ad88376035fbd5466b8243d9ba4ae628a2525c361bf4c6f64b98630 |
| remote_stderr.raw | 14 | 580172f071a944333f1fa16b45aa79564d5d82f899568fcf8cad46ddbb58c804 |
| ssh_stdout_transport.raw | 2470 | b38640da73d45f5212b1fe4553e7682a70c901fb0462250192d3af878a29904a |
| ssh_client.log.raw | 4525 | 30db83c5499e4e0f680ee812da25dc495c325165a3811721f8e2f1cdf90b6e69 |

两条Payload均做bytes==bytes比较，随后独立验证大小和完整SHA，不只是比文本或长度。
NUL、stdout的ff/80、stderr的fe/81及换行逐字节保留，没有UTF-8解码重编码。

ssh_stderr_unclassified.raw为0字节，仍完整保留；本次没有混入本地诊断的实际bytes。
这不能推广为所有OpenSSH错误都被-E分流，今后异常路径仍按UNCLASSIFIED保存。
-E日志原始保存，不经过grep/head/tail过滤；目录0700、文件0600，不自动上传。

## 7. Wire、退出收据与时间线

2470字节Wire包含原V1 start/data/exit帧，不能当成55字节应用stdout。权威Decoder
验证Run ID、nonce、sequence、stream、offset、chunk/最终SHA和大小，恢复两流。
最后exit帧按原始行bytes单独保存为remote_exit_receipt.raw；不重新编码或改写收据。
收据来源和哈希见exit_status_audit.json及transport_verification.json。

combined_observed_timeline.jsonl共17个本地读取/还原观察事件，sequence连续，
monotonic_ns严格递增，UTC合法，累计字节、offset、chunk SHA全部核对。
remote_observed_timeline.jsonl保留发送端观察时间与本地接收时间的独立时钟域。
这些是读取观察顺序，不保证是远端真实write顺序，也不是网络报文逐包时间。

## 8. 退出状态验收

| 字段 | 实测 |
| --- | --- |
| ssh_transport_status | SUCCESS |
| ssh_process_return_code | 23 |
| remote_exit_status | 23 |
| remote_exit_status_observed | true |
| remote_command_status | COMPLETED_NONZERO |
| local_timeout | false |
| remote_timed_out | false |
| remote_termination_status | EXITED（核心原值） |
| remote_termination_confirmed | true（验收派生布尔值，来自reaped收据） |

非零23是本次特意选择的命令结果，不是SSH故障。远端23来自原始V1收据，不能仅凭
本地23推导；收据同时含真实PID/start identity、argv、executable、reaped和完整两流哈希。

## 9. Process Lifecycle与时间口径

本地SSH PID=1095782，start identity=35534241，返回23，reaped=true。
本地UTC提交时间2026-11-25T05:23:59.504025+00:00，结束05:24:14.508815+00:00，
即北京时间13:23:59.504025至13:24:14.508815。单调耗时15.837416706秒，预算30秒。
没有超时，没有terminate或kill调用。

远端Payload PID=403529，start identity=570366535，返回23，reaped=true；
远端UTC记录05:24:13.503977至05:24:13.520013，单调耗时0.017734362秒。
argv及可执行路径完整保存于原始收据，不含API Key。

UTC差值与单调耗时存在差异：本地UTC差15.004790秒，单调15.837417秒；
远端也有小幅差异。本轮保留原值，原因未调查，以单调字段作为elapsed，不修改时间戳，
不跨主机相减monotonic，也不将差值擅自解释为SSH/TLS网络根因。

## 10. Manifest与完整性

原适配器artifact_manifest.json含10项采集文件，原子发布后重新验证通过；其SHA-256：
96d1c62d2451a4c53a38cbea99d81d963745e409a02c53d8550093f051df4752。
未改写该清单或其原始文件。后续原始收据切片、验证结果、清理/启动审计、日报及
local_preflight/副本另用artifact_manifest_post_analysis.json统一覆盖，包含原Manifest，
排除自身以免自引用。追加清单无覆盖发布，全部文件fsync后逐项重新算SHA/大小。

文件总数、大小、路径、类型及ctime观察值见追加清单；ctime不冒充精确birth time。
transport_verification.json记录原始文件的具体信息。历史132份文件/11份Manifest与
本轮前后基线匹配，所有既有未提交文件保持不变。

## 11. 清理与资源边界

SSH自然退出23后已wait/reap，本地/proc/1095782不存在；stdin发送线程也已结束。
正常退出无需再terminate，避免对已回收/可能复用的PID发送信号；若超时，原共享
控制器才会验证本次句柄所有权后受控终止。本次没有向任何既有服务发送信号。

远端Payload的实际wait/reap由原始收据证明；外层Remote Command退出23由SSH状态
和日志确认，没有仅凭本地SSH结束就猜测远端已清理。没有额外连接去检查/杀进程。
没有Tunnel和任何转发，执行前后本地8000/8001监听表一致；没有关闭其他监听或
ControlMaster。Worker 0/1、vLLM、GPU及其他用户进程均未访问或修改。

## 12. 测试、扫描与异常

- 执行前相关专项回归：265 passed in 7.05s；stdin注入工厂Fake端到端通过。
- 执行后全仓：577 passed in 42.75s，0 failed，0 skipped。
- compileall（src/scripts/tests）、git diff --check通过。
- 核心源码、测试、冻结配置和历史哈希均未变化；任务证据敏感信息扫描通过。
- 原始证据没有真实API Key、私钥/证书材料；扫描只在内存比较，不记录凭据值或其摘要。
- 回归使用网络禁止hook；urllib3导入期本地IPv6 bind能力检查被阻止，未额外连接。

本次真实执行无捕获异常，无丢帧、收据缺失、字节差异或Manifest失败。
预检中的Fake记录明确在local_preflight/fake_factory/，不是第二次真实连接。
唯一实际启动的argv、源码SHA、工厂调用数和stdin发送结果均有独立审计。

命令：

```text
# 唯一真实连接，经已验收适配器及本次注入工厂执行，禁止再运行
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_transport_preflight/p2_d2_ssh_evidence_transport_20261125_002/execute_once.py --execute-authorized-once
# 执行后纯本地全仓回归
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_transport/p2_d2_ssh_evidence_transport_20261125_002/local_preflight/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

## 13. 事实、推断和未验证事项

直接证据：一次SSH句柄及退出23、publickey/known_hosts日志、原始Wire与两条字节完全
匹配的Payload、V1实际wait收据、本地回收和Manifest。程序控制流支持默认容器/uid
身份门禁通过；没有另行动态hostname测量，不把固定前缀单独当证明。

本次只证明该次真实SSH字节传输和证据链通过；不能推断所有错误路径均完成分流，
不能推断TLS Listener、证书、模型身份或历史Tunnel TLS超时已经解决。

SSH传输工程前置条件已具备，可提交审核是否授权下一次TLS Evidence Capture Stage B。
它仍需独立的Worker/CA/模型门禁和真实TLS验收；当前没有TLS成功证据，Full Five-Task
Gate仍未解除，不自动启动任何后续实验。

## 14. 交付与停止

Run：`runs/phase2_p2_d2_ssh_evidence_transport/p2_d2_ssh_evidence_transport_20261125_002/`。
保存完整原始流、收据、两级Manifest、启动/状态/清理审计及本日报副本。
没有改动001失败证据；没有Commit/Push。

最终 **SSH_EVIDENCE_TRANSPORT_STAGE_B_PASS**。本轮结束，停止等待下一次明确授权。
