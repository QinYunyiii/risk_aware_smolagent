# P2-D2 TLS Evidence & Classifier Audit — Offline Stage A

## 执行摘要与最终 Gate

日期：2026-11-25（Asia/Shanghai）。
最终 Gate：**TLS_CLASSIFIER_AUDIT_CONDITIONAL_PASS**。

本轮全部工作在本地离线完成。证据完整性、分类器定向修复和113项回归测试通过；
条件项是历史证据已经过筛选，没有未经筛选的OpenSSL stdout/stderr，无法恢复
被排除的连接摘要，也不足以证明3.0.2与3.0.13之间存在确定的输出差异。

可确认：原实现只接受一个完成标记，遗漏brief建连摘要和Finished消息头的观测，
且没有独立阻止fatal alert在其他成功条件成立时被判成功。这些是可离线回归的
解析/判据缺口。不能确认：历史握手实际失败、历史完整输出中没有建连摘要、
或者根因必定是OpenSSL小版本差异。

历史Stage B的原始分类保持 **DIAGNOSTIC_INCONCLUSIVE**；新旧分类器对实际保存的
同一组15条日志做离线解析，结果也均为DIAGNOSTIC_INCONCLUSIVE。
没有将缺失证据补成成功，更没有重跑历史Run。

## 1. 原始证据与完整性

源Run：`p2_d2_remote_local_tls_isolation_20261125_001`，目录：
`runs/phase2_p2_d2_remote_local_tls_isolation/p2_d2_remote_local_tls_isolation_20261125_001/`。

本轮实际读取了该目录全部15个文件，并核对：

- artifact_manifest.json：10/10条目SHA-256匹配；Manifest自身SHA-256为
  `0ca1ffe41dd1e81064cc455a404c1c3fefd3108f100150ef0b86f137d3a335f2`。
- artifact_manifest_post_analysis.json：14/14条目匹配，包含原Manifest，排除自身。
- tls_trace.jsonl与ssh_remote_command_audit.jsonl中的25条remote_event逐条一致。
- 其中15条包含OpenSSL summary、原始stream及时间，原样构成回归fixture。
- tls_trace.jsonl SHA-256：
  `b54f900c4923b045e23479ff1399449f3b3e98d4b206d3a7594496d7237b8f98`。
- gate_summary.json记录原分类不确定，OpenSSL exit=0、timed_out=false；
  resource_release.json记录诊断进程已回收。
- 修改前保存15个历史文件的哈希，修改后全部一致；没有覆盖历史Run或Manifest。

**证据限制：不存在未经筛选的OpenSSL stdout.txt/stderr.txt。**
ssh.stderr.txt保存的是SSH客户端诊断，而不是远端OpenSSL stderr；远端stdout经过
控制程序变成JSON事件。remote_stdin_program.py保存了实际发送的代码、解析规则和
命令参数。不能把源码中出现的字符串当成运行时曾经输出该字符串的证据。

本轮独立审计目录：
`runs/phase2_p2_d2_tls_classifier_audit/p2_d2_tls_classifier_audit_20261125_001/`。
保存修改前基线、分类器源码副本、差异、独立离线解析结果、测试记录和追加分析。
这些产物不是新的真实网络实验或训练数据。

## 2. OpenSSL实际记录分析

版本事件记录：OpenSSL 3.0.2 15 Mar 2022，Library同版本。
实际发送程序中的唯一握手命令如下；没有另存运行时/proc/cmdline快照，命令证据来自
执行程序的固定命令构造和相应进程结果，不声称另有独立argv采样。

```text
openssl s_client -connect 127.0.0.1:8000 -servername localhost \
  -verify_hostname localhost -verify_return_error \
  -CAfile /root/.config/risk-agent/tls/ca.crt -brief -state -msg
```

原CA哈希门禁通过。OpenSSL PID=401965，exit=0，未超时，单调耗时
0.119948355秒；SSH exit=0，耗时6.411906397秒。这些是实际记录，不是本轮测量。

| 待检查字段 | 已保存运行时日志 | 判断 |
| --- | --- | --- |
| CONNECTION ESTABLISHED | 未保存 | 原过滤器会丢弃；无法确定原进程是否输出 |
| Protocol version | 未保存 | 同上，不能从本地OpenSSL版本补造 |
| Ciphersuite | 未保存 | 同上 |
| Peer certificate | 未保存 | 不补造当前证书subject/SAN等信息 |
| Verification: OK | 存在，stderr | 证书验证结果成功，需与命令中的CA/hostname验证设置一起解读 |
| SSL_connect状态 | 存在，stderr | 包括read finished、write finished，属于状态字符串 |
| SSL negotiation finished successfully | 已保存日志中不存在 | 原过滤器会保留这个标记，但本次没有相应事件 |
| ServerHello、Certificate | 存在，stdout消息头 | 服务端握手消息已被客户端观测 |
| Finished消息头 | 未保存 | 原过滤器不保留Finished消息头；不能推断其未输出 |
| TLS Alert | 本端warning close_notify | 正常关闭通知；不单独证明握手完成，也不是fatal错误 |

`Verification: OK`是OpenSSL报告的证书验证成功结果。命令使用原CA、
-verify_hostname localhost及-verify_return_error，因此不是“忽略证书错误继续”的
配置。但这一行自身不证明Finished交换、SSL_connect成功返回或应用层HTTP可用。
日志中的“read server certificate verify”是TLS状态字符串，也不能替代这个验证结果。

`SSLv3/TLS read finished`和`write finished`是OpenSSL的通用状态名称，不能把其中的
SSLv3当成本次协商协议版本，也不把进入这些状态等同于已可靠完成整个握手。

## 3. TLS阶段证据表

| 事件 | 原始保留输出是否存在 | 是否能证明阶段完成 |
| --- | --- | --- |
| TCP Connected | 无CONNECTED原始行；有SSL_connect状态及ServerHello | TCP可用为强间接推断；无精确connect时间 |
| ClientHello Sent | 有>>> ClientHello消息头及write client hello状态 | 直接观测客户端发送侧握手消息；不证明对端收到 |
| ServerHello Received | 有<<< ServerHello消息头 | 直接观测客户端接收该消息 |
| Certificate Received | 有<<< Certificate消息头 | 直接观测证书消息接收 |
| Certificate Verified | 有Verification: OK | 直接报告验证结果成功；结合严格命令参数解释 |
| Finished Exchange | 有read/write finished状态，缺少保留的Finished消息头 | 有进展证据；完整交换不可独立确认 |
| Handshake Complete | 缺少明确完成标记/完整建连摘要 | 整体证据支持握手推进至末尾，但严格完成证据仍不足 |

因此当前不确定分类来自“所需完成证据未被识别/取得”；不能反向认定握手没有完成。
证书成功、exit=0、短耗时、close_notify叠加也不被当成替代完成标记。

## 4. 冻结分类器审计

文件：[remote_local_tls_isolation.py](../../../src/risk_agent/collection/remote_local_tls_isolation.py)。
冻结源码在独立审计目录remote_local_tls_isolation_before.py保留，差异另存
classifier_change.patch；历史Run里的执行源码不变。

旧成功条件为：exit=0、未超时、state.complete=true、state.verified=true；
证书错误/TCP失败/握手超时分支先行。因此旧实现并没有只按退出码判成功。
state.complete只在含SSL_connect:的行同时包含小写子串
`negotiation finished successfully`时置true。仅Verification: OK无法置true。

- stdout/stderr共用同一个line回调及状态，不存在分别运行两套分类器的问题。
- bounded_process分别读取两条pipe并按行切分；能处理CRLF，当前记录的消息头在stdout，
  SSL_connect和Verification在stderr，均已进入同一结果。未发现这次误拆流的证据。
- 已保存行没有成功标记的大小写/空格变体；不能归因于本次大小写或换行问题。
- 核心缺口是仅识别一个完成表达，且过滤器不保留brief连接摘要与Finished消息头。
- 现有合成成功测试人为提供该特定字符串，所以无法检出真实记录中的完成证据缺失。

## 5. 版本兼容性结论

实测证据只覆盖远端3.0.2的**筛选日志**；没有3.0.13的实际完整成功握手记录。
SSH横幅中的OpenSSL 3.0.13是本地SSH所链接的库，不是远端s_client输出格式证据。

本轮仅离线读取本地安装的OpenSSL资料：
/usr/share/man/man1/openssl-s_client.1ssl.gz说明-state输出状态、-msg输出协议消息、
-brief输出连接参数概要、-verify_return_error使验证错误导致失败；读取本地二进制
字符串也确认存在CONNECTION ESTABLISHED、Protocol version、Ciphersuite及验证摘要。
这能支持设计兼容解析，但不是一次3.0.13握手，更不能证明3.0.2原输出有这些字段。

结论：**单一标记及摘要过滤的兼容性脆弱点已确认；3.0.2与3.0.13的小版本差异
是否直接造成本次不确定结果，仍为UNKNOWN。**没有访问互联网或下载源码。

## 6. 最小修复与保守成功条件

仅修改remote_entry内的解析/分类块，SSH、OpenSSL命令、CA门禁、5秒预算、外部
进程管理、Checkpoint、其他连接模块及配置均不改。源码前缀/后缀及AST比较确认
其他函数不变。

1. 保留原明确完成状态，用锚定匹配防止无关子串被当成成功。
2. 增加安全保留和识别brief的CONNECTION ESTABLISHED、有效TLS协议及非NULL密码套件。
   三者共同构成另一种明确完成报告；缺任一项不按此分支设置complete。
3. 最终成功仍要求验证OK、exit=0、未超时、明确完成证据且无fatal alert。
   明确证书错误仍优先判证书失败。fatal alert阻止成功，close_notify不作为fatal。
4. 保留Finished消息头以便未来取证，但Finished状态或消息头均不单独设置complete。
5. 不修改参数以增加采集量，不保留hex dump、Session Master-Key或Ticket内容。

这不是用Verification: OK或exit=0放宽完成条件。完整brief摘要路径的回归样例是
**明确标记的合成输出**，仅证明解析功能，不证明真实部署会输出该路径需要的全部字段。
旧Stage B没有这些保留证据，因此离线解析后仍是不确定，原始结果不变。

新增真实fixture：
`tests/fixtures/tls/remote_loopback_openssl_3_0_2_retained.json`。
记录来源Run ID、源Trace和Manifest哈希、版本、15条原样stream/text及原进程结果，
不添加不存在的建连摘要。它是工程回归fixture，不是风险标签或正式实验数据。

## 7. 测试结果

最终选定套件：**113 passed，0 failed，0 skipped**。

| 分组 | 数量 | 结果 |
| --- | --- | --- |
| 新TLS分类器专项测试 | 44 | 全部通过 |
| 原Remote-Local TLS Isolation测试 | 22 | 全部通过，原测试文件未改 |
| Worker 0诊断、ConnectionLifecycle及Smoke相关回归 | 47 | 全部通过；仅Mock/本地假进程 |

覆盖完整成功合成输出、只有验证OK、ServerHello后未完成、fatal/close_notify、
TCP后TLS超时、证书错误、exit=0但无完成证据、真实3.0.2保留日志、3.0.13格式的
**合成**样例、分流/合流、CRLF、NULL密码套件、非零退出/超时、序列化远端解析一致性。
没有将同一个合成字符串换版本标签后声称为两版本真实端到端验证。

真实fixture在修改前、修改后分别用假Runner解析：均DIAGNOSTIC_INCONCLUSIVE。
这里是离线日志解析，不是重新执行历史Run。所有网络/握手/进程启动均由假Runner替代。

最终命令：

```text
.venv/bin/python runs/phase2_p2_d2_tls_classifier_audit/p2_d2_tls_classifier_audit_20261125_001/offline_test_guard.py -q tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py tests/test_worker0_tls_diagnostic.py tests/test_connection_lifecycle.py tests/test_connection_smoke.py --disable-warnings
.venv/bin/python -m compileall -q src/risk_agent/collection/remote_local_tls_isolation.py tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py
git diff --check
git diff --no-index --check runs/phase2_p2_d2_tls_classifier_audit/p2_d2_tls_classifier_audit_20261125_001/remote_local_tls_isolation_before.py src/risk_agent/collection/remote_local_tls_isolation.py
```

最终测试耗时3.36秒，编译及两种diff检查通过。防护入口通过Python audit hook禁止
真实socket连接/解析/发送/绑定及ssh/openssl/curl/wget进程。
发现urllib3导入时会尝试本地IPv6可用性bind检查；该操作在系统调用前被防护入口阻止，
没有发起连接或发送数据。为确定这一条拦截的来源，给本地防护入口增加调用栈记录，
再次运行同一套件仍113通过。test_results_03.json记录urllib3.util.connection._has_ipv6
及socket.bind调用链；阻止的bind尝试为1，连接/解析/发送尝试为0，远程工具执行为0。
原有进程生命周期测试只启动本地Python假子进程并受控结束。

中间检查如实记录：新增测试初稿与原22项合计64 passed；随后增加2项用例。
首次使用防护入口时未加入仓库根到sys.path，产生3个收集错误（无执行失败的测试）；
只修正本地防护入口导入路径，再运行得到上述113通过。初次记录保留在test_results.json，
首次完整通过记录为test_results_02.json，带拦截来源的最终记录为test_results_03.json；
没有删除或覆盖失败记录。没有因为这些本地检查
发起任何网络请求。未运行全仓测试，范围为独立分类器及相关连接回归。

## 8. Git与修改范围

- HEAD保持bfd74dacf523d4677bc662e2e92b3bd5c8c09a10；未fetch或联系GitHub。
- 修改已有未跟踪模块remote_local_tls_isolation.py的解析/分类块。
- 新增tests/test_tls_evidence_classifier.py、真实fixture及本报告。
- 原有10个未跟踪文件均保留；除上述模块的授权变更外没有覆盖其他既有修改。
- 两份冻结1024-token配置、独立入口、原有测试文件SHA-256不变。
- 新差异不依赖git diff能显示未跟踪文件：独立审计目录另有逐行patch及源码前后比较。
- 未暂存、Commit或Push，没有将原始连接日志、密钥或证书加入Git。

## 9. 资源使用与停止

本轮真实SSH=0、远端命令=0、TLS握手=0、HTTP网络请求=0、Qwen=0、Tunnel=0；
vLLM/GPU查询及修改均为0。没有远端资源需要清理。没有修改远端服务或历史结果。

下一阶段建议先审核本报告的证据限制与两种明确完成路径。若需要真实版本输出对照，
应另行设计、授权一次独立最小采样；本轮不会自动执行，也不以当前合成测试替代它。
当前历史SSH转发超时根因仍未确证；即使未来取得远端TLS成功，也不能直接将其归罪
于转发路径。

保持 **TLS_CLASSIFIER_AUDIT_CONDITIONAL_PASS**；停止等待审核。
