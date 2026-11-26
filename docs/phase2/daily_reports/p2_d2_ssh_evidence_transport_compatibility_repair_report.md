# P2-D2 SSH Evidence Transport — Compatibility Repair

## 执行摘要

日期：2026-11-25（Asia/Shanghai）。
最终 Gate：**SSH_EVIDENCE_COMPATIBILITY_REPAIR_PASS**。

本轮修复非零退出语义和字节测试发送入口，保留V1帧格式并加入46项兼容性测试。
专项及相关回归 **265 passed**；全仓 **577 passed in 41.71s，0 failed，0 skipped**。
Fake SSH端到端实际返回23，适配器验证远端收据23，分类为SUCCESS/COMPLETED_NONZERO，
stdout/stderr分别与指定55/14字节完全一致，独立客户端日志及Manifest验证通过。

**本轮没有真实SSH传输证据。**没有执行SSH/OpenSSL、TLS、HTTP、Qwen、Docker、
vLLM、GPU或远端操作，也没有运行ssh -V/-G。Fake/Python进程只在本地测试运行。
原Stage B BLOCKED和历史Run均保留，不事后改判，不Commit/Push。

## 1. 两个问题的实际调用链与根因

审计依据为修改前实际源码和测试，副本保存在本次独立审计目录。

**退出语义：**capture_ssh_transport → FrameDecoder.finish → on_finish设置valid →
exit_semantics。原实现即使有有效收据，也只在ssh_code==0时允许完成运输。
SSH=23/Remote=23因而落入FAILED_OR_INCOMPLETE。原有测试主要覆盖SSH=0配远端
非零，以及255歧义，没有断言双方均为23/1时的成功运输。

**Payload接线：**旧计划脚本直接buffer.write裸Payload → SSH stdout → on_chunk →
FrameDecoder.feed；后者只接受RISK_SSH_EVIDENCE_V1前缀、JSON行及终结收据，所以
裸SSH-EVIDENCE-V1数据必然拒绝。原V1 Encoder/Decoder本身已有相同格式，问题是
测试发送脚本绕过了它，并非需要发明第二套协议或放宽解码器。

修改前已向用户说明这两个调用链，并保存root_cause_before.json中的原23/23离线结果。

## 2. 修改文件与边界

- 修改src/risk_agent/collection/ssh_evidence_transport.py。
- 新增tests/test_ssh_evidence_compatibility.py，46项参数化/独立用例。
- 新增tests/fixtures/ssh/v1_stage_a_sample.json，从未修改的历史Mock Wire派生，带源哈希。
- 新增本报告及Git忽略的本地离线审计产物。

原Stage A测试文件未修改；TLS Evidence Capture、TLS Classifier、Checkpoint v2、
旧运行入口、B2/B3/B4协议及冻结预算/模型配置均未修改。
SSH argv、安全选项、10秒ConnectTimeout和40秒外部默认期限保持不变；没有把此前
建议15/30秒悄悄写入配置。原TLS/监督子进程5秒预算也保持不变。

## 3. Exit Status修复

原字段保留，新增remote_command_status和ssh_transport_detail。独立处理本地返回码、
传输状态、远端返回码/是否观测、命令状态、超时及终止状态。

只有evidence_valid=true，且收据returncode是int非bool、reaped=true、timed_out为bool
时，才认定远端状态已观测。实际接收路径的evidence_valid必须来自完整帧、顺序、
两流大小/SHA和终结收据校验；不能只凭SSH码构造成功收据。

| SSH码 | 可信远端码 | 传输状态 | 远端命令状态 |
| --- | --- | --- | --- |
| 0 | 0 | SUCCESS | COMPLETED |
| 23 | 23 | SUCCESS | COMPLETED_NONZERO |
| 1 | 1 | SUCCESS | COMPLETED_NONZERO |
| 255 | 未知 | FAILED_OR_UNKNOWN | UNKNOWN |
| 未知 | 未知 | UNKNOWN | UNKNOWN |
| 0 | 23 | SUCCESS | COMPLETED_NONZERO，兼容旧wrapper外层退出0 |
| 23 | 1 | FAILED_OR_UNKNOWN | COMPLETED_NONZERO，保留已可靠观测的远端1 |

不匹配的非零本地/远端码不会运输成功。local_timeout单独标LOCAL_TIMEOUT，不自动
补远端码；没有收据时remote_termination_status=UNKNOWN。可靠负返回码区分SIGNALED/
TERMINATED，远端超时收据区分TIMED_OUT，不把命令失败混成运输失败。

有独立有效收据时可以记录远端255，但不会因SSH=255自行补255，也不会判SUCCESS。
为保留既有有效收据场景的接口，SSH=255且有可靠收据时仍保留SSH_255_AMBIGUOUS；
无收据时使用表中的FAILED_OR_UNKNOWN，detail标明歧义。缺少/损坏收据时远端码均null。

## 4. 权威V1帧协议与兼容性

新增FrameEncoder状态机，复用原encode_frame/data_frame/exit_frame。produce_session
也改用该Encoder，删除了另一处手工拼装data字段的路径。生成的自包含wrapper从同一
组函数/class源码获得编码器，避免本地/生成端协议漂移。

协议名称、前缀和现有字段不变：

- 固定头RISK_SSH_EVIDENCE_V1，后接ASCII JSON及LF帧边界。
- 每帧带protocol、run_id、nonce、严格递增sequence，start→data*→exit。
- data带stream(stdout/stderr)、offset、byte_count、base64 payload、chunk_sha256。
- exit带实际returncode、reaped、timed_out和两流总大小/完整SHA，另存生命周期。
- Wire帧上限仍128 KiB；Encoder默认按64 KiB原始块拆分长Payload，不截断。
  64 KiB是发送分块大小，不是收紧旧Decoder的Payload接受上限；旧合法帧仍可解码。
- 单个低层编码结果若超出原接收上限，启动前/发送前明确拒绝。Decoder仍拒绝非法头、
  长度/offset/SHA不符、非法base64、错序、截断、缺收据和退出后的额外帧。

空Payload以零流大小和空SHA表示，不伪造内容。NUL、非UTF-8和跨帧UTF-8只当bytes，
不会文本解码后重建。多帧连续读取、任意Wire分块和跨帧多字节字符均有专项测试。
旧裸Payload仍被拒绝；修复的是发送入口，并没有加入无收据的宽松裸流成功路径。

新V1历史fixture来自Stage A合成样例，记录源Run、源Manifest哈希、Wire哈希和预期
Payload。新Decoder可还原原值，既有低层Encoder还可逐字节重建同一旧Wire。
没有修改历史Fixture或把历史期望改成新结果。

## 5. 统一字节测试入口

prepare_byte_probe(run_id, nonce)返回ByteProbePlan：源码、55字节stdout、14字节stderr、
预期退出23及协议名。构造计划只编译检查源码，不启动进程，不打开网络。
它默认核验容器36f825bcc5da和uid=0；身份不符在Payload子进程启动前退出90，不输出
测试Payload，也不伪造远端23收据。

一致的执行链为：

```text
一个SSH Remote Command中的Python监督wrapper
  → 身份门禁
  → 一个仅buffer.write/flush指定bytes的Python子命令
  → PopenPipeProcess + produce_session + FrameEncoder
  → SSH stdout上的V1 Wire
  → 原Stage A Adapter + FrameDecoder
  → remote_stdout.raw / remote_stderr.raw
```

子命令真实返回23并由监督者wait/reap后，才生成exit收据。外层wrapper随后传递23，
从而SSH返回23也能正确分类。没有把尚未退出的wrapper自身意向状态冒充reaped收据。
sys.executable在执行端求值，不把WSL虚拟环境路径猜作远端Python路径；环境只传
PATH/LANG/LC_ALL，不传API Key，无shell=True、无远端落盘或额外SSH。

这里Wire与原始应用Payload明确不同：ssh_stdout_transport.raw保留完整帧；
remote_stdout.raw和remote_stderr.raw只含解码后的指定原始bytes，不混入帧头/收据。
未分类SSH stderr和-E客户端日志仍独立，不通过删本地诊断制造stderr一致。

端到端Fake测试发现生成端遗漏STREAMS全局常量，导致PopenPipeProcess无法初始化；
本次从既有采集模块导入并序列化同一常量修复。该问题属于生成端接线缺口，未修改
TLS Evidence Capture。现在测试不仅加载定义，还实际执行生成程序、监督并回收本地
Payload子进程、验证终结收据，防止“源码可compile就当入口可用”。

## 6. Fake SSH端到端验收

测试使用本地Python Fake SSH可执行脚本和实际二进制PIPE，经过原capture_ssh_transport，
未使用真实OpenSSH。Fake身份门禁使用明确的本地测试身份参数，不冒充真实容器。
默认未来计划仍是远端容器/root门禁。

结果：

- 本地Fake SSH进程实际退出23，已wait/reap。
- 实际Payload子进程的退出收据为23，reaped=true，有PID/start identity及生命周期。
- ssh_transport_status=SUCCESS，remote_command_status=COMPLETED_NONZERO。
- remote_exit_status=23且observed=true，remote_termination_status=EXITED。
- stdout与指定55 bytes逐字节相等；stderr与指定14 bytes逐字节相等。
- 两条Payload、客户端日志独立保存，文件大小、SHA和Manifest均匹配。
- Wire与Payload不同，Payload没有帧头；未分类SSH stderr为空，未做删改。
- 错误身份测试返回90，无Payload/exit收据；远端码仍null，不伪造23。

可检查样例位于本次审计目录local_fake_sample/prepared_fake/；synthetic_end_to_end.json
明确synthetic=true、真实SSH=0。future_remote_byte_probe_not_executed.py保存默认未来
身份门禁源码，仅供审核，未执行。

## 7. 测试与完整验证

新增46项测试覆盖要求的退出矩阵、缺失/损坏收据、255、timeout/unknown、非零命令
不等于运输失败；空/ASCII/NUL/非UTF-8/超长/UTF-8跨帧、多帧和任意分块；非法帧头、
长度/offset/SHA错误、终结收据错误和旧Wire兼容；实际Fake SSH返回23的全链路与
错误身份无Payload；Manifest精确哈希及生成计划不执行任何命令。

原53项SSH专项、53项TLS Capture及TLS/连接相关回归不修改旧期望，全部通过。

| 检查 | 结果 |
| --- | --- |
| 兼容性专项＋SSH/TLS相关回归 | 265 passed in 6.91s，0 failed/skip |
| 全仓回归 | 577 passed in 41.71s，0 failed/skip |
| compileall（src/scripts/tests） | PASS |
| git diff --check及任务文件差异检查 | PASS |
| Manifest、文件及chunk哈希 | PASS |
| 敏感信息扫描 | PASS，不保存真实凭据值或摘要 |

命令：

```text
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_compatibility_repair/p2_d2_ssh_evidence_compatibility_repair_20261125_001/offline_test_guard.py -q tests/test_ssh_evidence_compatibility.py tests/test_ssh_evidence_transport.py tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py tests/test_worker0_tls_diagnostic.py tests/test_connection_lifecycle.py tests/test_connection_smoke.py --disable-warnings --tb=short
.venv/bin/python runs/phase2_p2_d2_ssh_evidence_compatibility_repair/p2_d2_ssh_evidence_compatibility_repair_20261125_001/offline_test_guard.py -q --disable-warnings --tb=short
.venv/bin/python -m compileall -q src scripts tests
git diff --check
```

开发过程保留：收据字段重复转发曾导致2项失败；修正后生成端STREAMS遗漏曾导致
1项端到端失败；修正后相关组合145通过，再扩充得到最终结果。手工留档样例最初因
相对Fake路径被安全校验拒绝、启动0次，随后在独立本地子目录使用绝对路径成功，
原准备文件保留。上述均不是远端实验失败，也未触发真实重试。

## 8. 历史证据、Git和资源

独立审计目录：
`runs/phase2_p2_d2_ssh_evidence_compatibility_repair/p2_d2_ssh_evidence_compatibility_repair_20261125_001/`。
保存修改前根因/源码、baseline、patch、专项/全仓结果、开发记录、Fake样例、默认
未来源码、完整性检查和Manifest。所有合成样例不进入正式风险训练数据。

104份历史文件和九份Manifest前后匹配；六份受保护的TLS Capture/Classifier/入口/
fixture/冻结配置哈希不变。除授权修改的ssh_evidence_transport.py外，所有既有
未提交文件不变；原Stage A测试文件也不变。历史Stage B仍BLOCKED，TLS历史fixture
仍DIAGNOSTIC_INCONCLUSIVE。

HEAD保持bfd74dacf523d4677bc662e2e92b3bd5c8c09a10；工作区既有未跟踪文件保留。
未暂存、Commit、Push或fetch，不修改远端Worker或研究协议。

全仓与专项测试加网络/真实远端工具禁止审计hook；urllib3导入期的本地IPv6 bind
能力探测被系统调用前阻止，没有外部连接/解析/发送。Fake程序为stdlib-only，本地
子进程不调用SSH/OpenSSL或HTTP；不执行真实远端包装器，不操作其他进程/ControlMaster。
最终通过样例的Fake父进程及其Payload子进程均有wait/reap证据。

## 9. 下一阶段前置条件与停止

两个兼容性阻塞均已在离线路径解决。下一次真实字节验收应使用prepare_byte_probe
产生的V1发送入口，对remote_stdout.raw/remote_stderr.raw进行逐字节比较，不能再
将裸Payload直接喂给V1解码器或把Wire当应用stdout。

未来必须单独获得Stage B授权，使用新Run/nonce，固定计划源码及其哈希，重新核验
Alias、known_hosts、专用密钥、无转发、-E、持久化和磁盘；采用已审核的唯一真实
工厂，以受控stdin发送源码。审核者应明确监督wrapper内部的一个字节发射子进程
与一个SSH Remote Command的区别，以及保留的10/40秒配置。遇到无收据、错帧、255
歧义、哈希失败或超时立即停止，禁止自动重连。

本轮没有验证服务器可达性、真实主机身份、OpenSSH远端-E行为或真实传输时序。
离线PASS不代替上述真实验收，更不授权TLS、Smoke、完整五任务Gate或P2-D3。

最终 **SSH_EVIDENCE_COMPATIBILITY_REPAIR_PASS**。停止等待用户单独审核和授权。
