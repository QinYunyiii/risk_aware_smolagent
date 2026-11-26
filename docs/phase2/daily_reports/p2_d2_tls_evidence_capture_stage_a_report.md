# P2-D2 TLS Evidence Capture — Stage A

## 1. 执行摘要

日期：2026-11-25（Asia/Shanghai）。最终 Gate：**TLS_EVIDENCE_CAPTURE_STAGE_A_PASS**。

新增独立字节证据采集模块、仅离线入口和53项专项测试；连同现有113项相关回归，
最终 **166 passed，0 failed，0 skipped**。本轮未改变TLS分类器、OpenSSL命令、CA、
SNI、hostname验证、5秒进程预算或冻结配置；历史Stage B仍为DIAGNOSTIC_INCONCLUSIVE。

本次没有产生任何新的真实TLS握手证据。所有新采集测试使用Mock Process、合成bytes
或本地匿名管道；没有SSH、远端命令、OpenSSL进程、HTTP、Qwen或GPU操作。
PASS表示离线采集能力通过，不代表远端TLS成功，也不授权Stage B。

## 2. 历史证据缺口及现有代码审计

已读取Stage B报告、Classifier Audit、remote_local_tls_isolation.py、旧CLI、
审计Writer和Manifest逻辑，并核对真实文件。

- 旧bounded_process本来用二进制PIPE读取，不是握手进程用了text=True。
  text=True仅出现在版本/help等元数据子命令中。
- 旧读取将bytes按换行拆分并decode('utf-8', 'replace')后交给分类器；分类器只保留
  白名单summary，并strip及限制1000字符。因此非UTF-8、换行、空行、未匹配行和
  hex dump不能从保存的JSON事件还原。
- 旧finally回收本次子进程，但异常时没有完整原始输出文件可保留；部分异常路径
  只有错误事件，SSH/子进程退出字段也可能不可得，不能补成0。
- 旧JSON审计有fsync；没有OpenSSL原始字节文件。旧Manifest只能列出已存在的文件，
  不能证明没有采集过的stdout/stderr完整。
- 原设计有意不保存敏感TLS字段，不能把历史过滤行为说成偶发磁盘丢失。
  本轮按新要求增加受限原始证据层，旧实现保持不变。

## 3. 新模块与原始bytes

新增：

- src/risk_agent/collection/tls_evidence_capture.py
- scripts/test_p2_d2_tls_evidence_capture.py
- tests/test_tls_evidence_capture.py
- 本报告。

capture_process要求显式提供process_factory；模块本身没有Popen启动调用，也没有
默认真实工厂。PopenPipeProcess只能包装调用者已拥有的二进制进程句柄。
调用者未来必须使用argv数组、shell=False、stdin=DEVNULL、二进制PIPE及无凭据环境；
工厂在交付句柄前若失败，必须自行回收其已经创建的子进程，不把半初始化所有权丢给采集器。

stdout.bin和stderr.bin是权威文件。每块bytes直接写入对应文件；短写循环补齐，
不解码、不替换、不删空行、不转换CRLF、不改变大小写、不截断正常输出。
每次写入执行fsync。Manifest记录两个文件各自的实际大小及完整SHA-256，空流有
合法空文件及空bytes哈希；没有启动成功时进程/字节数收据仍为null，不冒充已观察的0。

默认不生成可读stdout.txt/stderr.txt，避免把有损文本派生物或秘密复制品当成权威证据。
需要可读分析时应从验证后的原始文件另行派生，绝不覆盖.bin。

原始输出可能包含TLS会话信息，**不能一边脱敏原始bytes，一边声称原始哈希不变**。
因此raw文件只写在新建0700目录内，文件0600；不进入普通日志，不自动上传或发布。
Manifest明确raw_is_unredacted=true、automatic_publish_allowed=false。元数据使用现有
脱敏器；敏感argv标志、Bearer/密钥形式及带userinfo的URL在启动前拒绝，错误文本不直接
透传。TLS诊断无需API Key，不读密钥环境、不读取私钥。合成secret测试证明raw未变、
元数据/时间线不包含该secret；这不构成对任意未知秘密的自动识别保证。

## 4. 跨流观察时间线

combined_observed_timeline.jsonl每个读取块记录：
sequence、UTC、monotonic_seconds、clock_domain、stream、offset、chunk_bytes、
cumulative_stream_bytes、cumulative_total_bytes、chunk_sha256及顺序语义声明。

sequence全局递增；每流offset独立。观察时间在持久化前取得，raw先fsync，随后写入
该块的时间线收据并fsync。Popen适配器一次返回一个已读块，不预先批读另一条pipe。
时间线没有输出内容，不能反过来修改raw。

顺序仅代表父进程观测到的读取顺序，不能保证是子进程真实write顺序。
UTC和单调时钟带明确时钟域；未来远端读取时间与本地接收时间必须分域保存，不能
跨主机相减。当前Popen适配器还原的是管道bytes，不是网络报文逐包时间。

## 5. 生命周期管理

process_result.json及lifecycle.jsonl记录：

- argv、实际采样executable path、PID及/proc start identity。
- factory提交UTC/monotonic、句柄接收时间、退出观察时间、采集结束时间和耗时。
- returncode、timed_out、termination_signal、terminate_called、kill_called、reaped。
- 所有权确认状态、实际stdout/stderr字节数、capture_complete和异常类型/阶段。

factory提交时间不是精确exec时间，/proc start ticks是独立进程身份采样；采样缺失
明确为null/unavailable。退出观察时间也不是精确内核退出时刻，不伪造精度。
正常、非零退出及负返回码均完整保存；CAPTURE_COMPLETE只表示证据采集完整，
不表示命令成功或TLS成功。

只对工厂交付的本次句柄操作。terminate前核对PID start identity，kill前再次核对。
所有权不明或发生变化时不发送信号，标记未回收/采集失败；只关闭本次管道与文件，
不冒称已经清理了那个进程。已退出的本次子进程仍调用wait取得退出状态。

## 6. 超时、异常与存储上限

准备文件及持久化capture_prepared在启动工厂之前完成；之后开始固定5秒总进程预算，
包括工厂提交/进程启动。独立Timer watchdog保证慢审计回调期间仍能触发本次进程回收，
主读取循环同时检查单调deadline。没有增加握手等待时间或调用自动重试。

超时先fsync已读raw，再对所有权确认的句柄terminate，等待0.25秒；仍未退出则再次
确认身份后kill并wait，回收等待上限1秒。信号及wait记录独立保存。回收和最多0.5秒
的尾部pipe drain属于清理开销，不是额外TLS预算；后代继承pipe导致EOF不可得时明确失败，
不无限等待。内核/存储调用永久阻塞不能由用户态承诺硬实时，此处未模拟系统失效为正常成功。

读/写/fsync/审计错误均中止继续采集并清理本次子进程，保留已写字节和可写出的失败
收据。异常只保存安全的类型/阶段，外部异常可能包含秘密，因此不把任意异常message
直接打印为普通日志。若磁盘持续不可写，失败收据/Manifest可能也无法完成；调用必须
抛错，保留已有文件，绝不补造完整报告。

默认stdout+stderr合计上限8 MiB；超过上限的块不写入，记录StorageLimitExceeded，
capture_complete=false，不宣称是完整输出。时间线等每个元数据文件也有独立上限：
max(64 KiB, min(raw_limit×8, 8 MiB))，防止大量碎块耗尽磁盘。上限是采集安全阈值，
没有修改实验模型/Token配置；任何超限都不静默截断后PASS。

## 7. SSH与远端命令证据边界

本轮没有调用ssh -G、ssh -help或任何SSH命令，未执行OpenSSL版本/help/握手。
没有假设-E可用，也没有启用它；其兼容性不作为本轮PASS条件。

直接SSH时，stdout通常是远端命令stdout；SSH客户端诊断与远端stderr会共享本地
stderr管道，**不能仅凭该管道声称二者已天然分离**。旧ssh.stderr.txt正说明必须
区分采集层。退出状态也必须分开：

| 字段/文件 | 含义与来源 |
| --- | --- |
| ssh_client_returncode | 本地SSH句柄wait结果；255不能当成OpenSSL退出码 |
| local_outer_timeout | 本地SSH监督期限，与5秒OpenSSL预算独立 |
| ssh_client_and_unattributed.stderr.bin | 未独立分流时明确保留来源歧义，不冒充OpenSSL stderr |
| remote_command_exit_status | 远端控制程序明确终结收据；缺失标unknown |
| remote_openssl_returncode/signal | 仅可信控制程序的本次OpenSSL wait收据 |
| stdout.bin、stderr.bin | 未来只能由明确标识的OpenSSL原始流还原，不能直接重命名SSH混合流 |

Stage B传输设计：一个SSH Remote Command内由只读控制程序读取OpenSSL两条binary
pipe，将原始块无损base64封装为带Run ID、递增seq、stream、offset、chunk SHA-256、
远端时钟域和长度的帧，统一经远端stdout发送；本地验证帧后还原.bin，并保存接收时间。
远端OpenSSL stderr也必须走这一带来源帧通道，避免混入SSH客户端stderr。控制程序
stdout之外的bootstrap错误保留为来源未完全判明的stderr，不能据此推断TLS失败。
缺帧、乱序、哈希错误、缺终结收据或SSH中断全部禁止成功分类。

该SSH framing/relay是**下一阶段的传输设计，本轮未接线、未进行真实端到端验证**。
若另选-E，需要先独立验证兼容性及哪些错误仍进入stderr，不能直接宣称完全分流。
SSH退出0也不能代替OpenSSL终结收据；远端Python捕获TLS错误后自身仍可能退出0。

## 8. 安全创建、原子发布和审计失败保护

目录逐层使用dirfd、O_DIRECTORY、O_NOFOLLOW访问；Run ID仅允许安全单一组件，
拒绝..、斜杠、反斜杠、绝对Run ID和含..的base。Run独占mkdir，不接管既有目录；
文件O_CREAT|O_EXCL|O_NOFOLLOW创建，防止覆盖和符号链接跳转。

启动工厂之前，两个raw、时间线、生命周期和结果文件均已安全创建并fsync，目录也
fsync；capture_prepared事件必须成功落盘。准备阶段任一关键I/O失败，工厂调用数为0。
启动后关键审计失败则终止继续读取/执行路径，只回收该句柄；不会发送下一次命令。

Manifest先写独占artifact_manifest.pending并fsync，再使用no-clobber硬链接原子发布
为artifact_manifest.json，移除本次pending并fsync目录。失败时保留pending，绝不以
部分JSON作为最终Manifest；已有同名最终文件不会被覆盖。raw是流式持久化，不能
声称整个进程所有文件属于单一文件系统事务；最终Manifest是完整证据的发布边界。

## 9. Manifest与分类器隔离

Manifest包含stdout.bin、stderr.bin、combined_observed_timeline.jsonl、lifecycle.jsonl、
process_result.json的SHA-256及大小；不包含自身以避免自引用。
verify_manifest同时校验文件、chunk SHA、sequence/offset、累计字节、时钟域、
单调顺序和完整捕获时的字节覆盖情况。缺文件/篡改/哈希不符必须失败。

verified_raw_streams先做上述校验，再要求capture_complete、reaped且无错误，才交付
原始两流及进程结果。即便exit非零/超时也要保留其实际状态，不能仅消费成功字符串。
完整性验证与完整捕获不同：故障后的部分文件可以有有效Manifest，但不能作为完整
TLS成功证据交给下游。

原分类器及CLI一字未改；没有调整成功、失败或不确定条件，也没有变更OpenSSL参数。
历史真实fixture在现有分类器下仍为DIAGNOSTIC_INCONCLUSIVE。

## 10. 离线测试

最终：53项新增 + 113项相关回归 = **166 passed in 4.48s，0 failed，0 skipped**。
相关回归包含TLS Classifier、Remote-Local Isolation、Worker 0诊断、ConnectionLifecycle
和Smoke；这些名称不代表执行了真实Smoke。

| 必需场景 | 验证方式/结果 |
| --- | --- |
| stdout、stderr、交错输出、空stdout/空stderr | 原样bytes与各自哈希/大小，PASS |
| 非UTF-8、多字节UTF-8跨块、CRLF/空行 | bytes完全一致，PASS |
| 大输出分块、短写、零进展写 | 无正常截断；无法写入明确失败，PASS |
| 正常/非零/信号退出 | returncode、signal和wait收据，PASS |
| 超时、terminate成功、terminate失败后kill | Mock最终wait/reap，先持久化后发信号，PASS |
| 慢审计独立watchdog | 加速测试Timer且生产常量仍5秒，PASS |
| stdout/stderr读取异常 | 原有字节保留、本次句柄回收，PASS |
| 创建/写入/审计/fsync失败 | 启动前不调用工厂，启动后回收并失败，PASS |
| Manifest哈希不符、pending写失败、最终文件冲突 | 拒绝消费、不发布半个Manifest、不覆盖，PASS |
| 超出raw/metadata存储上限 | 明确StorageLimitExceeded，PASS |
| 路径穿越、符号链接、重复Run | 不启动、不覆盖，PASS |
| 不同Run并发创建 | 字节、路径、Manifest互相隔离，PASS |
| 敏感argv/元数据、私有raw | 拒绝命令/安全元数据，raw字节不改且禁止发布，PASS |
| 所有权改变、后代pipe无EOF、早关pipe后挂起 | 不误杀、清理有界且不伪报回收，PASS |
| 历史fixture | 内容不变、分类仍不确定，PASS |

首轮开发测试25失败/10通过，原因是最终生命周期事件重复传clock_domain导致不能写出
结果；修正字段构造后35项通过，再补充适配器/原子发布/边界测试。最终全部通过。
没有将开发失败当成正式实验结果，也没有因本地失败重跑任何真实实验。

最终命令：

```text
.venv/bin/python runs/phase2_p2_d2_tls_evidence_capture_stage_a/p2_d2_tls_evidence_capture_stage_a_20261125_001/offline_test_guard.py -q tests/test_tls_evidence_capture.py tests/test_tls_evidence_classifier.py tests/test_remote_local_tls_isolation.py tests/test_worker0_tls_diagnostic.py tests/test_connection_lifecycle.py tests/test_connection_smoke.py --disable-warnings --tb=short
.venv/bin/python -m compileall -q src/risk_agent/collection/tls_evidence_capture.py scripts/test_p2_d2_tls_evidence_capture.py tests/test_tls_evidence_capture.py
.venv/bin/python scripts/test_p2_d2_tls_evidence_capture.py
git diff --check
```

编译和默认离线入口通过。入口只支持offline_preflight，拒绝remote_tls_diagnostic，
不提供Stage B模式或默认真实工厂。三个新增源码文件另对/dev/null做no-index --check：
无空白错误输出，返回1表示新增文件差异，不能误写为命令返回0。

最终套件使用Python audit hook禁止真实socket连接/解析/发送/绑定以及SSH/OpenSSL等
工具启动；记录了1次被阻止的urllib3导入期IPv6 bind能力探测，连接/解析/发送及远程
工具执行为0。相关旧测试仅启动本地Python假子进程，新采集测试不启动真实子进程。
新适配器测试仅用匿名pipe传输合成bytes。未重跑整个项目测试。

## 11. 历史不变性

独立审计目录：
`runs/phase2_p2_d2_tls_evidence_capture_stage_a/p2_d2_tls_evidence_capture_stage_a_20261125_001/`。

baseline.json记录修改前HEAD、Git状态、26项历史文件哈希、三份Manifest验证及5份
受保护源码/配置/fixture哈希。最终复核全部匹配；原Stage B两个Manifest的10/14条目
和Classifier Audit的10条目均通过，没有修改这些历史目录。
历史数据、分类器、旧运行入口、原始fixture及两份1024-token冻结配置保持不变。

同目录另有synthetic_raw_capture/可检查样例：仅Mock句柄，含非UTF-8、CRLF、空行及
跨块UTF-8的两条原始流、观察时间线、生命周期和独立Manifest。已验证还原bytes和
SHA-256一致；synthetic_example_receipt.json明确actual_process_launches=0、
tls_handshakes=0，不把样例PID或argv当成真实进程执行证据。

## 12. Git状态

HEAD仍为bfd74dacf523d4677bc662e2e92b3bd5c8c09a10。原有未提交文件保留，
本轮仅新增模块、离线入口、测试和本报告，审计产物位于Git忽略的runs目录。
未暂存、Commit、Push、fetch或访问GitHub；没有修改研究协议、预算或其他生产模块。

## 13. 远程资源使用

本轮SSH=0、远端命令=0、Tunnel=0、OpenSSL进程=0、真实TLS=0、HTTP=0、Qwen=0、
vLLM/GPU查询或修改=0。没有新增远端资源或文件，不需要远端清理。
本地测试中所有权丢失场景使用Mock对象，不存在因此遗留的真实进程。

## 14. Stage B计划与停止

Stage B必须获得新的明确授权。届时先在本地完成并审核上述无损framing/relay接线和
离线传输回归，验证binary/base64还原、缺帧中断及终结收据；不能直接运行旧白名单
入口后声称已经启用新raw证据层。

未来获批后的最小方案：新唯一Run；本地关键文件先持久化；一次严格身份校验SSH
Remote Command内检查容器、CA SHA-256、OpenSSL参数及Worker 0监听/进程，全部通过
再一次原参数、5秒预算的OpenSSL握手。无远端文件持久化，无HTTP/Qwen/Tunnel。
远端helper通过帧传输raw和生命周期，本地完整验证并fsync；各层退出/超时独立记录。
本地磁盘失败或连接中断时停止继续实验，由远端本次OpenSSL watchdog进行清理；
缺失远端回收收据则标记unknown，不能由本地SSH退出推断远端已回收，不追加SSH重试。

本轮不实现真实执行入口、不发起上述验证，也不更改分类器以获取PASS。
最终 **TLS_EVIDENCE_CAPTURE_STAGE_A_PASS**，停止等待审核。
